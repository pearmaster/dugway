import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import sleep
from typing import Any

import paho.mqtt.client as mqtt_client
import paho.mqtt.properties as props
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.packettypes import PacketTypes

from . import expectations
from .capabilities import (
    FromStep,
    JsonContentCapability,
    JsonMultiContentCapability,
    JsonSchemaDefinedCapability,
    JsonSchemaExpectation,
    JsonSchemaFilter,
    ServiceDependency,
)
from .meta import JsonConfigType, JsonSchemaType
from .runner import DugwayRunner
from .service import Service
from .step import TestStep

logger = logging.getLogger(__name__)

# How long to wait for the broker to acknowledge a connection or subscription.
BROKER_ACK_TIMEOUT_SECONDS = 10


class MqttPropertiesComparingCapability(JsonSchemaDefinedCapability):

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
        return True


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
        payload: str | None,
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
        if result != mqtt_client.MQTT_ERR_SUCCESS:
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

    Give either json for the payload, or nullPayload to send an empty message.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        serv_dep_cap = ServiceDependency(runner, config)
        super().__init__(runner, config, [serv_dep_cap])
        self._topic = config.get("topic")
        self._qos = config.get("qos", 0)
        self._retain = config.get("retain", False)
        if "json" in config:
            self._payload = json.dumps(config["json"])
        else:
            self._payload = None

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic to publish to.",
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
                        "json": {"description": "The payload, which is sent as JSON."},
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

    def run(self):
        pub_args = [self._topic, self._payload, self._qos, self._retain]
        self._runner._reporter.step_info("MQTT Publish", pub_args)
        mqtt_service = self.get_capability(ServiceDependency.NAME).get_service()
        if mqtt_service.is_v5 and (pub_prop_config := self._config.get("publishProperties", False)):
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
            pub_args.append(pub_props)

        mqtt_service.publish(*pub_args)


class MqttSubscribe(TestStep):
    """Subscribes to an MQTT topic, and keeps collecting messages while later steps run.

    Received messages must be JSON. An mqtt_message step that gives this step's id as 'from'
    checks the messages received so far.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        serv_dep_cap = ServiceDependency(runner, config)
        self._json_filter = JsonSchemaFilter(runner, config)
        self._json_multi = JsonMultiContentCapability(runner, config)
        self._mqtt_prop_comp = MqttPropertiesComparingCapability(runner, config, "filter")
        super().__init__(
            runner,
            config,
            [serv_dep_cap, self._json_multi, self._json_filter, self._mqtt_prop_comp],
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
        # so errors are queued for the step that consumes the messages.
        try:
            self._handle_message(message)
        except Exception as e:  # noqa: BLE001 - must not escape paho's thread
            self._logger.debug("Error handling message via %s: %s", message.topic, e)
            self._json_multi.add_error(e)

    def _handle_message(self, message):
        self._logger.debug("Received message via %s", message.topic)
        if not self._json_filter.check_against_json_schema(message.payload):
            self._logger.debug("Filtered out a message that didn't validate against json schema")
            return
        if not self._mqtt_prop_comp.properties_match(message.properties):
            self._logger.debug("Filtered out a message that didn't match MQTTv5 properties")
            return
        try:
            deserialized_json = json.loads(message.payload)
        except (json.decoder.JSONDecodeError, UnicodeDecodeError):
            raise expectations.ExpectationFailure(
                f"Message on '{message.topic}' was not JSON",
                "JSON Formatted Message",
                message.payload,
            )
        properties = {"topic": message.topic}
        self._json_multi.add_content(deserialized_json, properties)

    def run(self):
        mqtt_service = self.get_capability(ServiceDependency.NAME).get_service()
        topic = self._runner.template_eval(self._config.get("topic"))
        qos = int(self._runner.template_eval(self._config.get("qos", 0)))
        self._runner._reporter.step_info("MQTT Subscribe", topic)
        mqtt_service.subscribe(topic, qos, self._receive_message)


class MqttMessage(TestStep):
    """Checks the messages received by an mqtt_subscribe step, or the JSON from a json step.

    Checked messages are removed from the subscription, so a later mqtt_message step sees only
    the messages that are left.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        from_step = FromStep(runner, config)
        self._js_expect = JsonSchemaExpectation(runner, config)
        super().__init__(runner, config, [from_step, self._js_expect])

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
        # A 'json' step provides both capabilities, but only fills in the one matching its source.
        json_content_cap = from_step.find_capability(JsonContentCapability.NAME)
        if json_content_cap is not None and json_content_cap.json_content is not None:
            self.check_json(json_content_cap.json_content)
        elif json_multi := from_step.find_capability(JsonMultiContentCapability.NAME):
            if (expect_count := self._config.get("expect", {}).get("count")) is not None:
                while timeout_time is None or timeout_time > datetime.now(UTC):
                    json_multi.raise_first_error()
                    if expect_count == json_multi.count:
                        break
                    else:
                        logger.debug("Waiting for message")
                        sleep(1)
                else:
                    failure = expectations.ExpectationFailure("Message count", expect_count, json_multi.count)
                    raise failure
            json_multi.raise_first_error()
            consume_count = self._config.get("consume", "all")
            if consume_count == "all":
                consume_count = json_multi.count
            for _ in range(consume_count):
                json_content = json_multi.get_content()
                self.check_topic(json_content.properties.get("topic"))
                self.check_json(json_content.content)
        else:
            raise expectations.TestStepMissingCapability("No JsonMultiContent or JsonContent capability found.")
