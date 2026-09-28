"""AsyncAPI support: a service for an MQTT API described by an AsyncAPI document, and steps that
publish and subscribe to its operations and check messages against the document.

AsyncAPI 2.x and 3.x documents are both understood. Only MQTT servers are supported, since MQTT
is the message broker protocol Dugway speaks.

Besides message payloads, the document's MQTT bindings and message headers are checked. Headers
are carried as MQTTv5 user properties, and the bindings describe the connection, quality of
service, retain flag and MQTTv5 publish properties. Checks on MQTTv5 features are skipped on
connections using an earlier protocol version, which can't carry them.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jacobsjsonschema import draft7
from jacobsjsonschema.draft4 import JsonSchemaValidationError

from .api_spec import load_api_document
from .capabilities import ServiceDependency
from .expectations import ExpectationFailure, FailedTestStep, InvalidTestConfig
from .meta import JsonConfigType, JsonSchemaType
from .mqtt import MqttPublish, MqttService, MqttSubscribe, OutgoingMessage, received_properties
from .openapi import OpenApi30SchemaValidator, serialize_parameter
from .runner import DugwayRunner

# Each MQTT protocol name AsyncAPI uses, and whether it means TLS
MQTT_PROTOCOLS = {"mqtt": False, "mqtt5": False, "mqtts": True, "secure-mqtt": True}

# AsyncAPI protocolVersion values, as the protocol numbers the mqtt service accepts
MQTT_PROTOCOL_VERSIONS = {"3.1": 3.1, "3.1.0": 3.1, "3.1.1": 3.11, "5": 5, "5.0": 5, "5.0.0": 5}

# The operation direction words of each AsyncAPI version, by whether Dugway publishes for them
PUBLISHING_ACTIONS = {"send", "publish"}
SUBSCRIBING_ACTIONS = {"receive", "subscribe"}

ADDRESS_PARAMETER = re.compile(r"{([^}]+)}")


@dataclass
class AsyncApiOperation:
    """An operation found in an AsyncAPI document, in the same shape for 2.x and 3.x documents."""

    operation_id: str
    # The document's word for the operation's direction: send or receive, or publish or subscribe
    action: str
    address: str | None
    parameters: dict[str, Any]
    # Each message the operation may carry, with the name it is known by
    messages: list[tuple[str, dict[str, Any]]]
    # The operation's MQTT binding, such as its qos
    binding: dict[str, Any] = field(default_factory=dict)


def mqtt_binding(element: dict[str, Any] | None) -> dict[str, Any]:
    """The MQTT binding of a server, operation or message, or {} when it has none."""
    return dict(((element or {}).get("bindings") or {}).get("mqtt") or {})


def _message_label(message: dict[str, Any], index: int) -> str:
    return str(message.get("messageId") or message.get("name") or message.get("title") or f"message {index + 1}")


def _integer(value: Any) -> int | None:
    """A binding value that is an integer. Bindings may give a Schema Object instead, which is ignored."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def broker_defaults(spec: dict[str, Any], server_name: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The mqtt service options that the document's chosen server gives, such as its hostname, and
    the last will its MQTT binding gives.

    The named server is used when given, and otherwise the first MQTT server the document lists.
    """
    servers = spec.get("servers") or {}
    if server_name is not None:
        if server_name not in servers:
            raise InvalidTestConfig(
                f"The AsyncAPI document has no server named '{server_name}'. It has: {', '.join(servers) or 'none'}"
            )
        server = servers[server_name]
        if str(server.get("protocol", "")).lower() not in MQTT_PROTOCOLS:
            raise InvalidTestConfig(
                f"Server '{server_name}' uses the protocol '{server.get('protocol')}', but only MQTT is supported"
            )
    else:
        server = next((s for s in servers.values() if str(s.get("protocol", "")).lower() in MQTT_PROTOCOLS), None)
        if server is None:
            return {}, {}
    protocol = str(server.get("protocol")).lower()
    # AsyncAPI 3 gives a host, and AsyncAPI 2 a URL, either of which may use server variables
    address = str(server.get("host") or server.get("url") or "")
    for name, variable in (server.get("variables") or {}).items():
        address = address.replace(f"{{{name}}}", str(variable.get("default", "")))
    parts = urlsplit(address if "://" in address else f"//{address}")
    defaults: dict[str, Any] = {"tls": MQTT_PROTOCOLS[protocol]}
    if parts.hostname:
        defaults["hostname"] = parts.hostname
    if parts.port:
        defaults["port"] = parts.port
    version = MQTT_PROTOCOL_VERSIONS.get(str(server.get("protocolVersion", "")))
    if protocol == "mqtt5":
        version = 5
    if version is not None:
        defaults["protocol"] = version

    binding = mqtt_binding(server)
    if isinstance(binding.get("clientId"), str):
        defaults["clientId"] = binding["clientId"]
    if isinstance(binding.get("cleanSession"), bool):
        defaults["cleanSession"] = binding["cleanSession"]
    if (keep_alive := _integer(binding.get("keepAlive"))) is not None:
        defaults["keepAlive"] = keep_alive
    connect_properties = {
        name: value
        for name in ("sessionExpiryInterval", "maximumPacketSize")
        if (value := _integer(binding.get(name))) is not None
    }
    if connect_properties:
        defaults["connectProperties"] = connect_properties
    return defaults, dict(binding.get("lastWill") or {})


def validator_for(schema: Any, schema_format: str | None):
    """A validator for a schema in the given AsyncAPI schemaFormat, where None means JSON Schema."""
    # AsyncAPI 3 gives a schema in another format as a Multi Format Schema Object
    if isinstance(schema, dict) and "schemaFormat" in schema and "schema" in schema:
        schema_format, schema = schema["schemaFormat"], schema["schema"]
    fmt = str(schema_format or "").lower()
    if not fmt or fmt.startswith(("application/vnd.aai.asyncapi", "application/schema+")):
        return draft7.Validator(schema)
    if fmt.startswith("application/vnd.oai.openapi"):
        return OpenApi30SchemaValidator(schema)
    raise FailedTestStep(
        f"A message schema uses the schemaFormat '{schema_format}', but only JSON Schema and OpenAPI schemas "
        "are supported"
    )


def schema_problem(schema: Any, value: Any, schema_format: str | None = None) -> str | None:
    """Why the value doesn't match the schema, or None when it does."""
    try:
        validator_for(schema, schema_format).validate(value)
    except JsonSchemaValidationError as e:
        return str(e)
    return None


class AsyncApiService(MqttService):
    """An MQTT broker whose channels and messages are described by an AsyncAPI document, which
    asyncapi_publish and asyncapi_subscribe steps use by operationId.

    The document is loaded along with the suite, with its $refs resolved. Its MQTT server gives
    the hostname, port, TLS and protocol version. The server's MQTT binding gives the client id,
    clean session, keep alive, session expiry interval, maximum packet size and last will. Any of
    the mqtt service's options given here take precedence over them.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        self._spec = None
        last_will: dict[str, Any] = {}
        # The schema for the whole suite is built from an instance without any config
        if spec_path := config.get("spec"):
            self._spec = load_api_document(
                runner, runner.template_eval(spec_path), "AsyncAPI", "asyncapi", ("2.", "3.")
            )
            defaults, last_will = broker_defaults(self._spec, config.get("server"))
            connect_properties = {**defaults.get("connectProperties", {}), **config.get("connectProperties", {})}
            config = {**defaults, **config}
            if connect_properties:
                config["connectProperties"] = connect_properties
        super().__init__(runner, config)
        if last_will.get("topic"):
            message = last_will.get("message")
            self.client.will_set(
                str(last_will["topic"]),
                payload=message if message is None or isinstance(message, str) else json.dumps(message),
                qos=int(last_will.get("qos", 0)),
                retain=bool(last_will.get("retain", False)),
            )

    def get_object_schema(self) -> JsonSchemaType:
        mqtt_properties = dict(super().get_object_schema()["properties"])
        from_server = "Defaults to what the document's server gives."
        for name, description in {
            "hostname": f"Broker hostname or IP address. Templates are evaluated. {from_server}",
            "port": f"Broker port. {from_server}",
            "tls": "Connect using TLS. Defaults to true when the document's server uses secure-mqtt.",
            "protocol": f"MQTT protocol version. {from_server}",
            "clientId": f"Client identifier. Templates are evaluated. {from_server}",
            "cleanSession": "Start without any session the broker kept from before. Sent as clean start for "
            f"MQTTv5. {from_server}",
            "keepAlive": f"Most seconds between messages to the broker before a ping is sent. {from_server}",
        }.items():
            option = {k: v for k, v in mqtt_properties[name].items() if k != "default"}
            mqtt_properties[name] = {**option, "description": description}
        return {
            "properties": {
                "spec": {
                    "type": "string",
                    "description": "Path to the AsyncAPI 2 or 3 document, as YAML or JSON, relative to the suite "
                    "file. Templates are evaluated.",
                },
                "server": {
                    "type": "string",
                    "description": "Name of the document's server to connect to. Defaults to the first MQTT server "
                    "it lists.",
                },
                **mqtt_properties,
            },
            "required": ["spec"],
        }

    @property
    def spec(self):
        return self._spec

    def find_operation(self, operation_id: str) -> AsyncApiOperation:
        if str(self._spec.get("asyncapi", "")).startswith("2."):
            return self._find_v2_operation(operation_id)
        return self._find_v3_operation(operation_id)

    def _find_v2_operation(self, operation_id: str) -> AsyncApiOperation:
        # In AsyncAPI 2, a channel's key is its address, and its operations are publish and subscribe
        for address, channel in (self._spec.get("channels") or {}).items():
            for action in ("publish", "subscribe"):
                operation = channel.get(action)
                if operation is None or operation.get("operationId") != operation_id:
                    continue
                message = operation.get("message")
                if message is None:
                    messages = []
                elif "oneOf" in message:
                    messages = list(message["oneOf"])
                else:
                    messages = [message]
                return AsyncApiOperation(
                    operation_id,
                    action,
                    address,
                    dict(channel.get("parameters") or {}),
                    [(_message_label(m, i), m) for i, m in enumerate(messages)],
                    mqtt_binding(operation),
                )
        raise FailedTestStep(f"The AsyncAPI document has no operation with operationId '{operation_id}'")

    def _find_v3_operation(self, operation_id: str) -> AsyncApiOperation:
        # In AsyncAPI 3, operations are keyed by their id, and refer to their channel and messages
        operation = (self._spec.get("operations") or {}).get(operation_id)
        if operation is None:
            raise FailedTestStep(f"The AsyncAPI document has no operation with operationId '{operation_id}'")
        channel = operation.get("channel") or {}
        channel_messages = dict(channel.get("messages") or {})
        if "messages" in operation:
            # Known by their key in the channel, which the resolved references share
            messages = [
                (
                    next((k for k, m in channel_messages.items() if m is message), None) or _message_label(message, i),
                    message,
                )
                for i, message in enumerate(operation["messages"])
            ]
        else:
            messages = list(channel_messages.items())
        return AsyncApiOperation(
            operation_id,
            str(operation.get("action", "")),
            channel.get("address"),
            dict(channel.get("parameters") or {}),
            messages,
            mqtt_binding(operation),
        )

    @staticmethod
    def require_direction(operation: AsyncApiOperation, publishing: bool):
        """Fails unless the document's direction for the operation is the one the step uses: send or
        publish operations are published, and receive or subscribe operations are subscribed to.
        """
        if publishing and operation.action not in PUBLISHING_ACTIONS:
            raise FailedTestStep(
                f"Operation '{operation.operation_id}' is a '{operation.action}' operation in the AsyncAPI document, "
                "so it is subscribed to with asyncapi_subscribe, not published with asyncapi_publish"
            )
        if not publishing and operation.action not in SUBSCRIBING_ACTIONS:
            raise FailedTestStep(
                f"Operation '{operation.operation_id}' is a '{operation.action}' operation in the AsyncAPI document, "
                "so it is published with asyncapi_publish, not subscribed to with asyncapi_subscribe"
            )

    def address(self, operation: AsyncApiOperation, given: dict[str, Any], wildcard: bool) -> str:
        """The operation's channel address, with its parameters filled in.

        A parameter that isn't given takes its documented default, or when wildcard is true, becomes
        MQTT's single level wildcard.
        """
        if operation.address is None:
            raise FailedTestStep(f"The channel of operation '{operation.operation_id}' has no address")
        in_address = ADDRESS_PARAMETER.findall(operation.address)
        documented = {*operation.parameters, *in_address}
        if unknown := sorted(set(given) - documented):
            raise FailedTestStep(
                f"Operation '{operation.operation_id}' has no parameter named {', '.join(repr(n) for n in unknown)}. "
                f"It accepts: {', '.join(sorted(documented)) or 'none'}"
            )
        address = operation.address
        for name in in_address:
            parameter = operation.parameters.get(name) or {}
            # AsyncAPI 3 puts enum and default on the parameter, and AsyncAPI 2 in its schema
            schema = parameter.get("schema") or {}
            default = parameter.get("default", schema.get("default"))
            allowed = parameter.get("enum", schema.get("enum"))
            if name in given:
                value = serialize_parameter(given[name])
                if any(c in value for c in "/+#"):
                    raise FailedTestStep(
                        f"Parameter '{name}' is '{value}', but a topic level can't contain '/', '+' or '#'"
                    )
                if allowed is not None and value not in [serialize_parameter(a) for a in allowed]:
                    raise FailedTestStep(
                        f"Parameter '{name}' is '{value}', which is not one of the documented values: "
                        f"{', '.join(serialize_parameter(a) for a in allowed)}"
                    )
            elif wildcard:
                value = "+"
            elif default is not None:
                value = serialize_parameter(default)
            else:
                raise FailedTestStep(f"Operation '{operation.operation_id}' requires the parameter '{name}'")
            address = address.replace(f"{{{name}}}", value)
        return address

    @staticmethod
    def messages_for(operation: AsyncApiOperation, message_name: str | None) -> list[tuple[str, dict[str, Any]]]:
        """The operation's messages that a payload may be, narrowed to one when its name is given."""
        if message_name is None:
            return operation.messages
        named = [
            (label, message)
            for label, message in operation.messages
            if message_name in (label, message.get("name"), message.get("messageId"))
        ]
        if not named:
            raise FailedTestStep(
                f"Operation '{operation.operation_id}' has no message named '{message_name}'. "
                f"It has: {', '.join(label for label, _ in operation.messages) or 'none'}"
            )
        return named

    def match_message(
        self,
        operation: AsyncApiOperation,
        payload: Any,
        user_properties: dict[str, str] | None,
        message_name: str | None,
    ) -> tuple[tuple[str, dict[str, Any]] | None, list[str]]:
        """Finds which of the operation's messages the payload is, with its headers as the given user
        properties, or None when they aren't known. Gives the message and its name, or the reason
        each message didn't match when none did.
        """
        candidates = self.messages_for(operation, message_name)
        if not candidates:
            return None, []
        failures = []
        for label, message in candidates:
            problems = []
            payload_schema = message.get("payload")
            if payload_schema is not None and (
                problem := schema_problem(payload_schema, payload, message.get("schemaFormat"))
            ):
                problems.append(f"payload {problem}")
            # A message's schemaFormat is only for its payload, and headers are always AsyncAPI schemas
            headers_schema = message.get("headers")
            if (
                user_properties is not None
                and headers_schema is not None
                and (problem := schema_problem(headers_schema, user_properties))
            ):
                problems.append(f"headers (user properties) {problem}")
            if not problems:
                return (label, message), []
            failures.append(f"{label}: {', '.join(problems)}")
        return None, failures


def _without_topic(schema: dict[str, Any]) -> dict[str, Any]:
    """An mqtt step's schema, without the topic that the AsyncAPI document gives instead."""
    schema = dict(schema)
    schema["properties"] = {k: v for k, v in schema["properties"].items() if k != "topic"}
    schema["required"] = [r for r in schema.get("required", []) if r != "topic"]
    return schema


def _operation_properties(parameters_description: str, message_description: str) -> dict[str, Any]:
    return {
        "operationId": {
            "type": "string",
            "description": "The operationId of the operation, as given in the AsyncAPI document. Templates are "
            "evaluated.",
        },
        "parameters": {
            "type": "object",
            "description": parameters_description,
        },
        "message": {
            "type": "string",
            "description": message_description,
        },
    }


# Message binding fields that are the value of the MQTTv5 property with the same name
FIXED_MESSAGE_BINDINGS = ("payloadFormatIndicator", "contentType")


class AsyncApiPublish(MqttPublish):
    """Publishes a message to the channel of an asyncapi service's send or publish operation, after
    checking it against the AsyncAPI document.

    The topic is the channel's address, with its parameters filled in. The step fails before
    publishing if the operation isn't one the document says is sent or published, a parameter
    isn't documented or has no value, or the json payload and user properties don't match one of
    the operation's messages, as its payload and headers.

    The qos, retain and publish properties default to what the operation's and message's MQTT
    bindings give, and the step fails if it gives a different value. A nullPayload is published
    without checking the payload or headers, since an empty message is how a retained message is
    cleared.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        super().__init__(runner, config)

    def get_object_schema(self) -> JsonSchemaType:
        schema = _without_topic(super().get_object_schema())
        properties = dict(schema["properties"])
        properties["qos"] = {
            "type": "integer",
            "description": "Quality of service level: 0, 1 or 2. Defaults to the operation's MQTT binding, or 0.",
        }
        properties["retain"] = {
            "type": "boolean",
            "description": "Have the broker keep this as the topic's retained message. Defaults to the "
            "operation's MQTT binding, or false.",
        }
        properties["publishProperties"] = {
            **properties["publishProperties"],
            "description": "MQTTv5 properties to publish with, including userProperties for the message's headers. "
            "Defaults to what the MQTT bindings give. Ignored by other protocol versions.",
        }
        schema["properties"] = {
            **_operation_properties(
                "Values for the channel address's parameters, by name. A parameter that isn't given takes its "
                "documented default. Templates in string values are evaluated.",
                "Name of the operation's message the payload must be, when it has several. By default the "
                "payload may be any of them.",
            ),
            **properties,
        }
        schema["required"] = ["operationId", *schema["required"]]
        return schema

    def outgoing_message(self) -> OutgoingMessage:
        service = self.get_capability(ServiceDependency.NAME).get_service()
        operation = service.find_operation(self._runner.template_eval(self._config["operationId"]))
        service.require_direction(operation, publishing=True)
        parameters = self._runner.template_eval_all(dict(self._config.get("parameters", {})))
        topic = service.address(operation, parameters, wildcard=False)
        properties = self._runner.template_eval_all(dict(self._config.get("publishProperties", {})))
        qos = self._bound(operation, "qos", self._config.get("qos"), 0)
        retain = self._bound(operation, "retain", self._config.get("retain"), False)
        if "json" not in self._config:
            return OutgoingMessage(topic, None, qos, retain, properties if service.is_v5 else {})

        payload = self._runner.template_eval_all(self._config["json"])
        # User properties only exist in MQTTv5, so headers can't be checked on earlier versions
        user_properties = dict(properties.get("userProperties", {})) if service.is_v5 else None
        matched, failures = service.match_message(operation, payload, user_properties, self._config.get("message"))
        if failures:
            raise FailedTestStep(
                f"The message does not match any message of operation '{operation.operation_id}' in the AsyncAPI "
                f"document: {'; '.join(failures)}"
            )
        if service.is_v5:
            properties = self._bound_properties(operation, matched[1] if matched else None, properties)
        if matched is not None:
            self._runner._reporter.step_info("AsyncAPI message", matched[0])
        return OutgoingMessage(topic, json.dumps(payload), qos, retain, properties if service.is_v5 else {})

    @staticmethod
    def _bound(operation: AsyncApiOperation, name: str, given: Any, default: Any) -> Any:
        """The value the operation's MQTT binding gives, which a value the step gives must agree with."""
        documented = operation.binding.get(name)
        if name == "qos":
            documented = _integer(documented)
        elif not isinstance(documented, bool):
            documented = None
        if documented is None:
            return default if given is None else given
        if given is not None and given != documented:
            raise FailedTestStep(
                f"The step gives {name} {json.dumps(given)}, but the MQTT binding of operation "
                f"'{operation.operation_id}' gives {json.dumps(documented)}"
            )
        return documented

    @staticmethod
    def _bound_properties(
        operation: AsyncApiOperation, message: dict[str, Any] | None, properties: dict[str, Any]
    ) -> dict[str, Any]:
        """The publish properties, with defaults from the MQTT bindings filled in, after checking that
        the ones the step gives agree with them.
        """
        properties = dict(properties)
        documented = {"messageExpiryInterval": _integer(operation.binding.get("messageExpiryInterval"))}
        binding = mqtt_binding(message)
        for name in FIXED_MESSAGE_BINDINGS:
            documented[name] = binding.get(name)
        # A response topic is either a fixed topic, or a schema that any topic given must match
        response_topic = binding.get("responseTopic")
        documented["responseTopic"] = response_topic if isinstance(response_topic, str) else None
        for name, value in documented.items():
            if value is None:
                continue
            if name in properties and properties[name] != value:
                raise FailedTestStep(
                    f"The step gives the publish property {name} {json.dumps(properties[name])}, but the AsyncAPI "
                    f"document's MQTT binding gives {json.dumps(value)}"
                )
            properties[name] = value
        for name, schema in (("responseTopic", response_topic), ("correlationData", binding.get("correlationData"))):
            if (
                isinstance(schema, dict)
                and name in properties
                and (problem := schema_problem(schema, properties[name]))
            ):
                raise FailedTestStep(
                    f"The publish property {name} does not match the AsyncAPI document's MQTT binding: {problem}"
                )
        return properties


class AsyncApiSubscribe(MqttSubscribe):
    """Subscribes to the channel of an asyncapi service's receive or subscribe operation, and checks
    every message received against the AsyncAPI document while later steps run.

    The topic filter is the channel's address, with its parameters filled in, and any parameter
    that isn't given matches every value. The step fails if the operation isn't one the document
    says is received or subscribed to.

    A received message's payload and user properties must match one of the operation's messages,
    as its payload and headers. Its qos, message expiry interval and MQTTv5 properties must agree
    with the operation's and message's MQTT bindings. An mqtt_message step that gives this step's
    id as 'from' fails on a message that doesn't, before making its own checks. Messages removed
    by the filter aren't checked.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        super().__init__(runner, config)
        self._service: AsyncApiService | None = None
        self._operation: AsyncApiOperation | None = None

    def get_object_schema(self) -> JsonSchemaType:
        schema = _without_topic(super().get_object_schema())
        properties = dict(schema["properties"])
        properties["qos"] = {
            "type": "integer",
            "description": "Highest quality of service level to receive messages at. Defaults to the operation's "
            "MQTT binding, or 0, and can't be lower than the binding's.",
        }
        schema["properties"] = {
            **_operation_properties(
                "Values for the channel address's parameters, by name. A parameter that isn't given matches "
                "every value, using MQTT's '+' wildcard. Templates in string values are evaluated.",
                "Name of the operation's message that received messages must be, when it has several. By "
                "default a message may be any of them.",
            ),
            **properties,
        }
        schema["required"] = ["operationId", *schema["required"]]
        return schema

    def subscription_topic(self) -> str:
        self._service = self.get_capability(ServiceDependency.NAME).get_service()
        self._operation = self._service.find_operation(self._runner.template_eval(self._config["operationId"]))
        self._service.require_direction(self._operation, publishing=False)
        # Checked now, so that a wrong name fails this step rather than every message
        self._service.messages_for(self._operation, self._config.get("message"))
        parameters = self._runner.template_eval_all(dict(self._config.get("parameters", {})))
        return self._service.address(self._operation, parameters, wildcard=True)

    def subscription_qos(self) -> int:
        documented = _integer(self._operation.binding.get("qos"))
        given = self._config.get("qos")
        if given is None:
            return documented or 0
        given = int(self._runner.template_eval(given))
        # A lower qos would have the broker downgrade the messages, so their qos couldn't be checked
        if documented is not None and given < documented:
            raise FailedTestStep(
                f"The step subscribes at qos {given}, below the qos {documented} that the MQTT binding of "
                f"operation '{self._operation.operation_id}' gives"
            )
        return given

    def _failure(self, problem: str, expected: Any, actual: Any, topic: str) -> ExpectationFailure:
        return ExpectationFailure(
            f"Message on '{topic}' does not match operation '{self._operation.operation_id}' in the AsyncAPI "
            f"document: {problem}",
            expected,
            actual,
        )

    def check_message(self, payload: Any, properties: dict[str, Any], message):
        topic = properties.get("topic")
        is_v5 = self._service.is_v5
        received = received_properties(getattr(message, "properties", None))
        user_properties = received["userProperties"] if is_v5 else None
        matched, failures = self._service.match_message(
            self._operation, payload, user_properties, self._config.get("message")
        )
        if failures:
            raise self._failure(
                f"it matches none of its messages. {'; '.join(failures)}",
                "A message matching one of: " + ", ".join(label for label, _ in self._operation.messages),
                json.dumps({"payload": payload, "userProperties": user_properties}),
                topic,
            )
        documented_qos = _integer(self._operation.binding.get("qos"))
        received_qos = getattr(message, "qos", None)
        if documented_qos is not None and received_qos is not None and received_qos != documented_qos:
            raise self._failure(
                "its qos differs from the operation's MQTT binding", documented_qos, received_qos, topic
            )
        if is_v5:
            self._check_bound_properties(matched[1] if matched else None, received, topic)
        if matched is not None:
            properties["message"] = matched[0]

    def _check_bound_properties(self, message: dict[str, Any] | None, received: dict[str, Any], topic: str):
        documented_expiry = _integer(self._operation.binding.get("messageExpiryInterval"))
        received_expiry = received["messageExpiryInterval"]
        # The broker counts the interval down while it holds the message
        if documented_expiry is not None and (received_expiry is None or received_expiry > documented_expiry):
            raise self._failure(
                "its message expiry interval isn't within the operation's MQTT binding",
                f"At most {documented_expiry}",
                received_expiry,
                topic,
            )
        binding = mqtt_binding(message)
        for name in FIXED_MESSAGE_BINDINGS:
            if (documented := binding.get(name)) is not None and received[name] != documented:
                raise self._failure(
                    f"its {name} property differs from the message's MQTT binding", documented, received[name], topic
                )
        for name in ("responseTopic", "correlationData"):
            documented = binding.get(name)
            if documented is None:
                continue
            if received[name] is None:
                raise self._failure(
                    f"it has no {name} property, which the message's MQTT binding describes", documented, None, topic
                )
            problem = (
                received[name] != documented
                if isinstance(documented, str)
                else schema_problem(documented, received[name])
            )
            if problem:
                raise self._failure(
                    f"its {name} property doesn't match the message's MQTT binding", documented, received[name], topic
                )
