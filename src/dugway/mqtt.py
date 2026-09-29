import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import sleep
from typing import Any, Annotated

import paho.mqtt.client as mqtt_client
import paho.mqtt.properties as props
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.packettypes import PacketTypes

from . import expectations
from .capabilities import (
    ConversionCapability,
    ContentWithProperties,
    FromStep,
    JsonSchemaDefinedCapability,
    JsonSchemaExpectation,
    JsonSchemaFilter,
    RawMultiContentCapability,
    MultiValueCapability,
    ServiceDependency,
    multi_values,
    single_value,
)
from .meta import JsonConfigType, JsonSchemaType
from .runner import DugwayRunner
from .service import Service
from .step import TestStep

logger = logging.getLogger(__name__)

# How long to wait for the broker to acknowledge a connection or subscription.
BROKER_ACK_TIMEOUT_SECONDS = 10


class MqttPropertiesComparingCapability(JsonSchemaDefinedCapability):
    """Ignores received messages whose MQTTv5 properties don't match the ones given as
    'filter.publishProperties'.
    """

    NAME = "MqttProperties"

    def __init__(self, runner, config: JsonConfigType, parent_json_property: str):
        self._parent_json_property = parent_json_property
        super().__init__(self.NAME, runner, config)

    @classmethod
    def publish_property_schema(cls) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "payloadFormatIndicator": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "0 when the payload is unspecified bytes, 1 when it is UTF-8 text.",
                },
                "messageExpiryInterval": {
                    "type": "integer",
                    "description": "Seconds before the broker discards the message if undelivered.",
                },
                "responseTopic": {"type": "string", "description": "Topic that a response should be sent to."},
                "correlationData": {
                    "type": "string",
                    "description": "Data that ties a response to its request.",
                },
                "contentType": {"type": "string", "description": "Content type of the payload."},
                "userProperties": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "User properties, as names and values.",
                },
            },
        }

    def get_config_schema(self) -> JsonSchemaType:
        schema = {
            "type": "object",
            "properties": {
                self._parent_json_property: {
                    "type": "object",
                    "properties": {
                        "publishProperties": {
                            **self.publish_property_schema(),
                            "description": "MQTTv5 publish properties that must match.",
                        },
                    },
                },
            },
        }
        return schema

    def properties_match(self, pub_props) -> bool:
        def received(prop_name):
            return getattr(pub_props, prop_name, None)

        parent_obj = self._config.get(self._parent_json_property, {})
        if (expected_pub_props := parent_obj.get("publishProperties", False)) is not False:
            if (p_f_i := expected_pub_props.get("payloadFormatIndicator", False)) is not False and p_f_i != received(
                "PayloadFormatIndicator"
            ):
                return False
            if (m_e_i := expected_pub_props.get("messageExpiryInterval", False)) is not False and m_e_i != received(
                "MessageExpiryInterval"
            ):
                return False
            if (r_t := expected_pub_props.get("responseTopic", False)) is not False and r_t != received(
                "ResponseTopic"
            ):
                return False
            if (c_d := expected_pub_props.get("correlationData", False)) is not False and c_d.encode() != received(
                "CorrelationData"
            ):
                return False
            if (c_t := expected_pub_props.get("contentType", False)) is not False and c_t != received("ContentType"):
                return False
            if expected_user_props := expected_pub_props.get("userProperties"):
                actual_user_props = dict(received("UserProperty") or [])
                if any(actual_user_props.get(k) != v for k, v in expected_user_props.items()):
                    return False
        return True


def received_properties(pub_props) -> dict[str, Any]:
    """The MQTTv5 properties of a received message, named as in publishProperties. Properties the
    message doesn't have are None, and user properties are a dict.
    """

    def received(prop_name):
        return getattr(pub_props, prop_name, None)

    correlation_data = received("CorrelationData")
    return {
        "payloadFormatIndicator": received("PayloadFormatIndicator"),
        "messageExpiryInterval": received("MessageExpiryInterval"),
        "responseTopic": received("ResponseTopic"),
        "correlationData": (
            correlation_data.decode(errors="replace") if isinstance(correlation_data, bytes) else correlation_data
        ),
        "contentType": received("ContentType"),
        "userProperties": dict(received("UserProperty") or []),
    }


@dataclass
class OutgoingMessage:
    """Everything about a message that an mqtt_publish step sends."""

    topic: str
    payload: bytes | None
    qos: int
    retain: bool
    # MQTTv5 properties, named as in publishProperties
    properties: dict[str, Any]


class MqttService(Service):
    """An MQTT broker connection, which mqtt_publish and mqtt_subscribe steps use.

    The service connects when it is set up, and waits for the broker to accept the connection.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        super().__init__(runner, config)
        kwargs = {"callback_api_version": CallbackAPIVersion.VERSION2}
        if client_id := config.get("clientId", False):
            kwargs["client_id"] = self._runner.template_eval(client_id)
        if protoc := config.get("protocol", False):
            kwargs["protocol"] = {
                3.1: MQTTProtocolVersion.MQTTv31,
                3.11: MQTTProtocolVersion.MQTTv311,
                5: MQTTProtocolVersion.MQTTv5,
            }[protoc]
        self.is_v5 = protoc == 5
        # MQTTv5 replaces clean session with clean start, which is given when connecting
        self._clean_start = config.get("cleanSession", None)
        if self._clean_start is not None and not self.is_v5:
            kwargs["clean_session"] = self._clean_start
        self.client = mqtt_client.Client(**kwargs)
        if config.get("tls", False):
            self.client.tls_set()
        if credentials := config.get("credentials", False):
            self.client.username_pw_set(
                self._runner.template_eval(credentials["username"]),
                self._runner.template_eval(credentials["password"]),
            )
        self._subscriptions: list[str] = []
        # Acks arrive on paho's network thread; these let setup() and subscribe()
        # wait for them, so a later step can't race ahead of the broker.
        self._connack = threading.Event()
        self._connect_reason = None
        self._suback_lock = threading.Condition()
        self._subacks: dict[int, list] = {}
        self.client.on_connect = self._on_connect
        self.client.on_subscribe = self._on_subscribe

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self._connect_reason = reason_code
        self._connack.set()

    def _on_subscribe(self, client, userdata, mid, reason_code_list, properties):
        with self._suback_lock:
            self._subacks[mid] = reason_code_list
            self._suback_lock.notify_all()

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "hostname": {
                    "type": "string",
                    "default": "localhost",
                    "description": "Broker hostname or IP address. Templates are evaluated, "
                    "and an empty result means localhost.",
                },
                "port": {"type": "integer", "default": 1883, "description": "Broker port."},
                "tls": {"type": "boolean", "default": False, "description": "Connect using TLS."},
                "protocol": {
                    "type": "number",
                    "enum": [3.1, 3.11, 5],
                    "default": 3.11,
                    "description": "MQTT protocol version.",
                },
                "connectProperties": {
                    "type": "object",
                    "description": "MQTTv5 properties sent when connecting.",
                    "properties": {
                        "sessionExpiryInterval": {
                            "type": "integer",
                            "description": "Seconds the broker keeps the session after a disconnect.",
                        },
                        "receiveMaximum": {
                            "type": "integer",
                            "description": "Most QoS 1 and 2 messages the client will process at once.",
                        },
                        "maximumPacketSize": {
                            "type": "integer",
                            "description": "Largest packet, in bytes, the client will accept.",
                        },
                    },
                },
                "clientId": {
                    "type": "string",
                    "description": "Client identifier. Templates are evaluated. A random one is used if not given.",
                },
                "cleanSession": {
                    "type": "boolean",
                    "description": "Start without any session the broker kept from before. "
                    "Sent as clean start for MQTTv5.",
                },
                "keepAlive": {
                    "type": "integer",
                    "default": 60,
                    "description": "Most seconds between messages to the broker before a ping is sent.",
                },
                "credentials": {
                    "type": "object",
                    "description": "Username and password to connect with. Templates are evaluated.",
                    "properties": {
                        "username": {"type": "string", "description": "Username."},
                        "password": {"type": "string", "description": "Password."},
                    },
                    "required": ["username", "password"],
                },
            },
        }

    def setup(self):
        args = [
            self._runner.template_eval(self._config.get("hostname", "")) or "localhost",
            int(self._runner.template_eval(self._config.get("port", 1883))),
            self._config.get("keepAlive", 60),
        ]
        kwargs = {}
        if self.is_v5:
            if self._clean_start is not None:
                kwargs["clean_start"] = self._clean_start
            prop_config = self._config.get("connectProperties", {})
            connect_props = props.Properties(PacketTypes.CONNECT)
            if (s_e_i := prop_config.get("sessionExpiryInterval", False)) is not False:
                connect_props.SessionExpiryInterval = int(self._runner.template_eval(s_e_i))
            if (r_m := prop_config.get("receiveMaximum", False)) is not False:
                connect_props.ReceiveMaximum = int(self._runner.template_eval(r_m))
            if (m_p_s := prop_config.get("maximumPacketSize", False)) is not False:
                connect_props.MaximumPacketSize = int(self._runner.template_eval(m_p_s))
            if len(prop_config) > 0:
                kwargs["properties"] = connect_props
        self._logger.debug(f"MQTT connecting with {args} {kwargs}")
        self.client.connect(*args, **kwargs)
        self.client.loop_start()
        if not self._connack.wait(BROKER_ACK_TIMEOUT_SECONDS):
            raise ConnectionError(
                f"MQTT broker {args[0]}:{args[1]} did not acknowledge the connection "
                f"within {BROKER_ACK_TIMEOUT_SECONDS} seconds"
            )
        if self._connect_reason.is_failure:
            raise ConnectionError(f"MQTT broker {args[0]}:{args[1]} refused the connection: " f"{self._connect_reason}")

    def reset(self):
        for sub_topic in self._subscriptions:
            self.client.message_callback_remove(sub_topic)
        self._subscriptions = []

    def teardown(self):
        self.client.disconnect()
        self.client.loop_stop()

    def publish(
        self,
        topic: str,
        payload: bytes | None,
        qos: int = 0,
        retain: bool = False,
        properties: props.Properties | None = None,
    ):
        kwargs = {
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain,
        }
        if self.is_v5 and properties is not None:
            kwargs["properties"] = properties
        self.client.publish(**kwargs)

    def subscribe(
        self,
        sub_topic: str,
        qos: int,
        callback: Callable[[mqtt_client.Client, Any, str], None],
    ):
        self.client.message_callback_add(sub_topic, callback)
        self._subscriptions.append(sub_topic)
        result, mid = self.client.subscribe(sub_topic, qos)
        if result != mqtt_client.MQTT_ERR_SUCCESS or mid is None:
            raise expectations.FailedTestStep(f"Could not subscribe to {sub_topic}: {mqtt_client.error_string(result)}")
        with self._suback_lock:
            if not self._suback_lock.wait_for(lambda: mid in self._subacks, BROKER_ACK_TIMEOUT_SECONDS):
                raise expectations.FailedTestStep(
                    f"MQTT broker did not acknowledge the subscription to {sub_topic} "
                    f"within {BROKER_ACK_TIMEOUT_SECONDS} seconds"
                )
            reason_codes = self._subacks.pop(mid)
        if failures := [rc for rc in reason_codes if rc.is_failure]:
            raise expectations.FailedTestStep(f"MQTT broker refused the subscription to {sub_topic}: {failures[0]}")


class MqttPublish(TestStep):
    """Publishes a message to an MQTT broker.

    Give either json for the payload's value, or nullPayload to send an empty message. The value is
    sent as JSON unless a converter is given, such as a protobuf converter.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        serv_dep_cap = ServiceDependency(runner, config)
        self._conversion_cap = ConversionCapability(runner, config)
        super().__init__(runner, config, [serv_dep_cap, self._conversion_cap])
        self._topic = config.get("topic")
        self._qos = config.get("qos", 0)
        self._retain = config.get("retain", False)

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic to publish to.",
                    "minLength": "1",
                },
                "qos": {"type": "integer", "default": 0, "description": "Quality of service level: 0, 1 or 2."},
                "retain": {
                    "type": "boolean",
                    "default": False,
                    "description": "Have the broker keep this as the topic's retained message.",
                },
                "publishProperties": {
                    **MqttPropertiesComparingCapability.publish_property_schema(),
                    "description": "MQTTv5 properties to publish with. Ignored by other protocol versions.",
                },
            },
            "oneOf": [
                {
                    "properties": {
                        "json": {"description": "The payload's value, sent as JSON unless a converter is given."},
                    },
                    "required": ["json"],
                },
                {
                    "properties": {
                        "nullPayload": {
                            "type": "boolean",
                            "const": True,
                            "description": "Send an empty payload.",
                        }
                    },
                    "required": ["nullPayload"],
                },
            ],
            "required": [
                "topic",
            ],
        }

    def serialize_payload(self, value: Any) -> bytes:
        """The bytes to publish for the payload's value."""
        return self._conversion_cap.serialize(value)

    def outgoing_message(self) -> OutgoingMessage:
        """The message to publish, when the step runs."""
        if self._topic is None or len(self._topic) == 0:
            raise expectations.FailedTestStep("Cannot publish empty topic")
        payload = self.serialize_payload(self._config["json"]) if "json" in self._config else None
        return OutgoingMessage(
            self._topic, payload, self._qos, self._retain, dict(self._config.get("publishProperties", {}))
        )

    def run(self):
        message = self.outgoing_message()
        pub_args = [message.topic, message.payload, message.qos, message.retain]
        self._runner._reporter.step_info("MQTT Publish", pub_args)
        mqtt_service = self.get_capability(ServiceDependency.NAME).get_service()
        if mqtt_service.is_v5 and (pub_prop_config := message.properties):
            pub_props = props.Properties(PacketTypes.PUBLISH)
            if (p_f_i := pub_prop_config.get("payloadFormatIndicator", False)) is not False:
                pub_props.PayloadFormatIndicator = int(p_f_i)
            if (m_e_i := pub_prop_config.get("messageExpiryInterval", False)) is not False:
                pub_props.MessageExpiryInterval = int(m_e_i)
            if (r_t := pub_prop_config.get("responseTopic", False)) is not False:
                pub_props.ResponseTopic = str(r_t)
            if (c_d := pub_prop_config.get("correlationData", False)) is not False:
                pub_props.CorrelationData = c_d.encode()
            if (c_t := pub_prop_config.get("contentType", False)) is not False:
                pub_props.ContentType = str(c_t)
            if user_props := pub_prop_config.get("userProperties"):
                pub_props.UserProperty = [(str(k), str(v)) for k, v in user_props.items()]
            pub_args.append(pub_props)

        mqtt_service.publish(*pub_args)


def deserialize_payload(converter, payload: bytes, topic: str, config: JsonConfigType | None = None) -> Any:
    """Converts a received message's payload into a value, failing the step checking it when it can't."""
    try:
        return converter.deserialize(payload, config)
    except expectations.ExpectationFailure as e:
        raise expectations.ExpectationFailure(f"Message on '{topic}': {e}", e.expected, e.actual) from e


class MqttSubscribe(TestStep):
    """Subscribes to an MQTT topic, and keeps collecting messages while later steps run.

    Messages are kept as the bytes received. An mqtt_message step that gives this step's id as
    'from' checks the messages received so far, converting them from JSON or with the converter it
    is given. A deserialize step can also convert them, for steps such as jsonpath.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        serv_dep_cap = ServiceDependency(runner, config)
        self._json_filter = JsonSchemaFilter(runner, config)
        self._raw_multi: Annotated[RawMultiContentCapability, "All messages are provided via this capability."] = RawMultiContentCapability(runner, config)
        self._multi_value: Annotated[MultiValueCapability, "Filtered and parsed messages."] = MultiValueCapability(runner, config)
        self._conversion_cap = ConversionCapability(runner, config)
        self._mqtt_prop_comp = MqttPropertiesComparingCapability(runner, config, "filter")
        super().__init__(
            runner,
            config,
            [serv_dep_cap, self._raw_multi, self._json_filter, self._mqtt_prop_comp, self._conversion_cap, self._multi_value],
        )

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic filter to subscribe to, which may use wildcards. Templates are evaluated.",
                },
                "qos": {
                    "type": "integer",
                    "default": 0,
                    "description": "Highest quality of service level to receive messages at.",
                },
            }
        }

    def _receive_message(self, client: mqtt_client.Client, userdata: Any, message):
        # This runs in paho's network thread, where a raised exception would be lost,
        # so errors are queued for the steps that consume the messages.
        self._logger.debug("Received message via %s", message.topic)
        try:
            # Extract message properties
            pub_props = getattr(message, "properties", None)
            properties = {
                "topic": message.topic,
                "contentType": received_properties(pub_props)["contentType"],
            }

            # Always add raw message to rawmulticapability
            self._raw_multi.add_content(message.payload, properties)

            # Deserialize using conversion_cap (content-type is extracted from properties)
            content = ContentWithProperties(message.payload, properties)
            deserialized_value = self._conversion_cap.deserialize(content)

            # Check filters on deserialized value
            if not self._passes_filters_on_value(deserialized_value, pub_props):
                return

            # Call check_message hook (subclasses may modify properties)
            self.check_message(properties, message)

            # Add deserialized value to multivaluecapability
            self._multi_value.add_content(deserialized_value, properties)
        except Exception as e:  # noqa: BLE001 - must not escape paho's thread
            self._logger.debug("Error handling message via %s: %s", message.topic, e)
            self._raw_multi.add_error(e)

    def _passes_filters_on_value(self, deserialized_value: Any, pub_props) -> bool:
        """Checks if a deserialized value passes the configured filters (JSON schema and MQTT properties)."""
        # Check JSON schema filter on deserialized value
        json_text = json.dumps(deserialized_value)
        if not self._json_filter.check_against_json_schema(json_text):
            self._logger.debug("Filtered out a message that didn't validate against json schema")
            return False

        # Check MQTT properties filter
        if not self._mqtt_prop_comp.properties_match(pub_props):
            self._logger.debug("Filtered out a message that didn't match MQTTv5 properties")
            return False

        return True

    def check_message(self, properties: dict[str, Any], message):
        """Checks a received message that passed the filters, before it is kept. The message is
        paho's, with its payload, qos and MQTTv5 properties. Raising fails the step that consumes
        the messages. Subclasses may also add to its properties.
        """

    def subscription_qos(self) -> int:
        """The highest quality of service level to receive messages at, when the step runs."""
        return int(self._runner.template_eval(self._config.get("qos", 0)))

    def subscription_topic(self) -> str:
        """The topic filter to subscribe to, when the step runs."""
        return self._runner.template_eval(self._config.get("topic"))

    def run(self):
        mqtt_service = self.get_capability(ServiceDependency.NAME).get_service()
        topic = self.subscription_topic()
        qos = self.subscription_qos()
        self._runner._reporter.step_info("MQTT Subscribe", topic)
        mqtt_service.subscribe(topic, qos, self._receive_message)


class MqttMessage(TestStep):
    """Checks the messages received by an mqtt_subscribe or asyncapi_subscribe step, or the JSON from a
    deserialize step.

    A subscription's messages are converted from JSON, unless a converter is given, such as a
    protobuf converter.

    Checked messages are removed from the subscription, so a later mqtt_message step sees only
    the messages that are left.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        from_step = FromStep(runner, config)
        self._js_expect = JsonSchemaExpectation(runner, config)
        self._conversion = ConversionCapability(runner, config)
        super().__init__(runner, config, [from_step, self._js_expect, self._conversion])

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "consume": {
                    "oneOf": [
                        {"type": "integer", "minimum": 0},
                        {"type": "string", "const": "all"},
                    ],
                    "default": "all",
                    "description": "How many received messages to check and remove. The rest are kept for later steps.",
                },
                "timeoutSeconds": {
                    "type": ["number", "null"],
                    "default": None,
                    "description": "Most seconds to wait for expect.count messages. Waits indefinitely when null.",
                },
                "expect": {
                    "type": "object",
                    "description": "Checks made on the messages.",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "Wait until exactly this many messages have been received.",
                        },
                        "topic": {
                            "type": "string",
                            "description": "Fail unless each checked message arrived on this topic.",
                        },
                    },
                },
            },
        }

    def check_json(self, json_data: dict[str, Any]):
        self._js_expect.validate(json_data)

    def check_topic(self, topic: str):
        expected_topic = self._config.get("expect", {}).get("topic")
        if expected_topic and topic != expected_topic:
            raise expectations.ExpectationFailure("Received Topic", expected_topic, topic)

    def run(self):
        # With no timeout, wait for the expected message count indefinitely.
        timeout_time = None
        if (timeoutSeconds := self._config.get("timeoutSeconds", None)) is not None:
            timeout_time = datetime.now(UTC) + timedelta(seconds=timeoutSeconds)
        from_step = self.get_capability(FromStep.NAME).get_step()
        # A deserialize step provides one value or many, filling in the one matching its source
        is_single, value = single_value(from_step)
        if is_single:
            self.check_json(value)
        elif multi := multi_values(from_step) or from_step.find_capability(RawMultiContentCapability.NAME):
            # A subscription's messages are kept as received, so they are converted as they are checked
            is_raw = isinstance(multi, RawMultiContentCapability)
            if (expect_count := self._config.get("expect", {}).get("count")) is not None:
                while timeout_time is None or timeout_time > datetime.now(UTC):
                    multi.raise_first_error()
                    if expect_count == multi.count:
                        break
                    else:
                        logger.debug("Waiting for message")
                        sleep(1)
                else:
                    failure = expectations.ExpectationFailure("Message count", expect_count, multi.count)
                    raise failure
            multi.raise_first_error()
            consume_count = self._config.get("consume", "all")
            if consume_count == "all":
                consume_count = multi.count
            for _ in range(consume_count):
                content = multi.get_content()
                topic = content.properties.get("topic")
                self.check_topic(topic)
                value = content.content
                if is_raw:
                    converter = self._conversion.converter_for(content)
                    config = self._conversion.config_for(converter)
                    value = deserialize_payload(converter, content.content, topic, config)
                self.check_json(value)
        else:
            raise expectations.TestStepMissingCapability("No Value, MultiValue or RawMultiContent capability found.")
