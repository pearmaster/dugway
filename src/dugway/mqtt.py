
from typing import Callable, Any
import json
from datetime import datetime, timedelta
from time import sleep
import logging

import paho.mqtt.client as mqtt_client
import paho.mqtt.properties as props
from paho.mqtt.packettypes import PacketTypes
from jacobsjsonschema.draft7 import Validator as JsonSchemaValidator

from .runner import DugwayRunner
from .service import Service
from .step import TestStep
from .meta import JsonConfigType, JsonSchemaType
from .capabilities import (
    JsonSchemaDefinedCapability,
    ServiceDependency,
    JsonMultiContentCapability,
    FromStep,
    JsonSchemaExpectation,
    JsonSchemaFilter,
)
from . import expectations

logger = logging.getLogger(__name__)

class MqttPropertiesComparingCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType, parent_json_property: str):
        self._parent_json_property = parent_json_property
        super().__init__("MqttProperties", runner, config)
        
    @classmethod
    def publish_property_schema(cls) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "payloadFormatIndicator": {"type": "integer", "minimum": 0, "maximum": 1},
                "messageExpiryInterval": {"type": "integer"},
                "responseTopic": {"type":"string"},
                "correlationData": {"type":"string"},
                "contentType": {"type":"string"},
            },
        }
    
    def get_config_schema(self) -> JsonSchemaType:
        schema = {
            "type": "object",
            "properties": {
                self._parent_json_property: self.publish_property_schema(),
            },
        }
        return schema

    def properties_match(self, pub_props) -> bool:
        parent_obj = self._config.get(self._parent_json_property, dict())
        if (expected_pub_props := parent_obj.get('publishProperties', False)) is not False:
            if (p_f_i := expected_pub_props.get('payloadFormatIndicator', False)) is not False:
                if p_f_i != pub_props.PayloadFormatIndicator:
                    return False
            if (m_e_i := expected_pub_props.get('messageExpiryInterval', False)) is not False:
                if m_e_i != pub_props.MessageExpiryInterval:
                    return False
            if (r_t := expected_pub_props.get('responseTopic', False)) is not False:
                if r_t != pub_props.ResponseTopic:
                    return False
            if (c_d := expected_pub_props.get('correlationData', False)) is not False:
                if c_d.encode() != pub_props.CorrelationData:
                    return False
            if (c_t := expected_pub_props.get('contentType', False)) is not False:
                if c_t != pub_props.ContentType:
                    return False
        return True

class MqttService(Service):

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        super().__init__(runner, config)
        kwargs = dict()
        if client_id := config.get('clientId', False):
            kwargs['client_id'] = self._runner.template_eval(client_id)
        if protoc := config.get('protocol', False):
            kwargs['protocol'] = {
                3.1: mqtt_client.MQTTv31,
                3.11: mqtt_client.MQTTv311,
                5: mqtt_client.MQTTv5,
            }[protoc]
        self.is_v5 = (protoc == 5)
        clean_session = config.get('cleanSession', None)
        if clean_session is not None:
            kwargs['clean_session'] = clean_session
        self.client = mqtt_client.Client(**kwargs)
        if tls := config.get('tls', False):
            self.client.tls_set()
        if credentials := config.get('credentials', False):
            self.client.username_pw_set(self._runner.template_eval(credentials['username']), self._runner.template_eval(credentials['password']))
        self._subscriptions: list[str] = list()

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "hostname": {
                    "type": "string",
                },
                "port": {
                    "type": "integer"
                },
                "tls": {
                    "type": "boolean",
                    "default": False
                },
                "protocolVersion": {
                    "type": "number",
                    "enum": [3.1, 3.11, 5],
                    "default": 3.11,
                },
                "connectProperties": {
                    "type": "object",
                    "properties": {
                        "sessionExpiryInterval": {"type":"integer"},
                        "receiveMaximum": {"type":"integer"},
                        "maximumPacketSize": {"type":"integer"},
                    },
                },
                "clientId": {
                    "type": "string",
                },
                "cleanSession": {
                    "type": "boolean",
                },
                "keepAlive": {
                    "type": "integer",
                },
                "credentials": {
                    "type": "object",
                    "properties": {
                        "username": {"type":"string"},
                        "password": {"type":"string"},
                    },
                    "required": [
                        "username",
                        "password,"
                    ],
                },
            },
            "required": [
                "hostname",
            ],
        }

    def setup(self):
        args = [
            self._runner.template_eval(self._config['hostname']),
            int(self._runner.template_eval(self._config.get('port', 1883))),
            self._config.get('keepAlive', 60),
        ]
        kwargs = dict()
        if self.is_v5:
            prop_config = self._config.get('connectProperties', dict())
            connect_props = props.Properties(PacketTypes.CONNECT)
            if s_e_i := prop_config.get('sessionExpiryInterval', False) is not False:
                connect_props.SessionExpiryInterval = int(self._runner.template_eval(s_e_i))
            if r_m := prop_config.get('receiveMaximum', False) is not False:
                connect_props.ReceiveMaximum = int(self._runner.template_eval(r_m))
            if m_p_s := prop_config.get('maximumPacketSize', False) is not False:
                connect_props.MaximumPacketSize = int(self._runner.template_eval(m_p_s))
            if len(prop_config) > 0:
                kwargs['properties'] = connect_props
        self._logger.debug(f"MQTT connecting with {args} {kwargs}")
        self.client.connect(*args, **kwargs)
        self.client.loop_start()
    
    def reset(self):
        for sub_topic in self._subscriptions:
            self.client.message_callback_remove(sub_topic)
        self._subscriptions = list()

    def teardown(self):
        self.client.disconnect()
        self.client.loop_stop()

    def publish(self, topic:str, payload:str|None, qos:int=0, retain:bool=False, properties:props.Properties|None=None):
        kwargs = {
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain,
        }
        if self.is_v5 and properties is not None:
            kwargs['properties'] = properties
        self.client.publish(**kwargs)
    
    def subscribe(self, sub_topic:str, qos:int, callback:Callable[[mqtt_client.Client,Any,str], None]):
        self.client.message_callback_add(sub_topic, callback)
        self._subscriptions.append(sub_topic)
        self.client.subscribe(sub_topic, qos)

class MqttPublish(TestStep):

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        serv_dep_cap = ServiceDependency(runner, config)
        super().__init__(runner, config, [serv_dep_cap])
        self._topic = config.get('topic')
        self._qos = config.get('qos', 0)
        self._retain = config.get('retain', False)
        if (json_payload := config.get('json', None)) is not None:
            self._payload = json.dumps(json_payload)
        if config.get('nullPayload', False):
            self._payload = None

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "topic": {
                    "type": "string",
                },
                "qos": {
                    "type": "integer",
                    "default": 0
                },
                "retain": {
                    "type": "boolean",
                    "default": False
                },
                "publishProperties": MqttPropertiesComparingCapability.publish_property_schema(),
            },
            "oneOf": [
                {
                    "properties": {
                        "json": True,
                    },
                    "required": ["json"],
                },
                {
                    "properties": {
                        "nullPayload": {
                            "type": "boolean",
                            "const": True,
                        }
                    },
                    "required": ["null_payload"],
                },
            ],
            "required": [
                "topic",
            ],
        }

    def run(self):
        pub_args = [
            self._topic,
            self._payload,
            self._qos,
            self._retain
        ]
        self._runner._reporter.step_info(f"MQTT Publish", pub_args)
        mqtt_service = self.get_capability("ServiceDependency").get_service()
        if mqtt_service.is_v5 and (pub_prop_config := self._config.get('publishProperties', False)):
            pub_props = props.Properties(PacketTypes.PUBLISH)
            if (p_f_i := pub_prop_config.get('payloadFormatIndicator', False)) is not False:
                pub_props.PayloadFormatIndicator = int(p_f_i)
            if (m_e_i := pub_prop_config.get('messageExpiryInterval', False)) is not False:
                pub_props.MessageExpiryInterval = int(m_e_i)
            if (r_t := pub_prop_config.get('responseTopic', False)) is not False:
                pub_props.ResponseTopic = str(r_t)
            if (c_d := pub_prop_config.get('correlationData', False)) is not False:
                pub_props.CorrelationData = c_d.encode()
            if (c_t := pub_prop_config.get('contentType', False)) is not False:
                pub_props.ContentType = str(c_t)
            pub_args.append(pub_props)
        
        mqtt_service.publish(*pub_args)


class MqttSubscribe(TestStep):

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        serv_dep_cap = ServiceDependency(runner, config)
        self._json_filter = JsonSchemaFilter(runner, config)
        self._json_multi = JsonMultiContentCapability(runner, config)
        self._mqtt_prop_comp = MqttPropertiesComparingCapability(runner, config, "filter")
        super().__init__(runner, config, [serv_dep_cap, self._json_multi, self._json_filter])

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "topic": {
                    "type": "string",
                },
                "qos": {
                    "type": "integer",
                    "default": 0
                },
            }
        }
    
    def _receive_message(self, client: mqtt_client.Client, userdata: Any, message):
        self._logger.debug("Received message via %s", message.topic)
        if not self._json_filter.check_against_json_schema(message.payload.decode('utf-8')):
            self._logger.debug("Filtered out a message that didn't validate against json schema")
            return
        if not self._mqtt_prop_comp.properties_match(message.properties):
            self._logger.debug("Filtered out a message that didn't match MQTTv5 properties")
            return
        try:
            deserialized_json = json.loads(message.payload)
        except json.decoder.JSONDecodeError:
            raise expectations.ExpectationFailure("Message Format", "JSON Formatted Message". message.payload)
        properties = {
            "topic": message.topic
        }
        self._json_multi.add_content(deserialized_json, properties)

    def run(self):
        mqtt_service = self.get_capability("ServiceDependency").get_service()
        topic = self._runner.template_eval(self._config.get('topic'))
        qos = int(self._runner.template_eval(self._config.get('qos', 0)))
        self._runner._reporter.step_info(f"MQTT Subscribe", topic)
        mqtt_service.subscribe(topic, qos, self._receive_message)

class MqttMessage(TestStep):

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        from_step = FromStep(runner, config)
        self._js_expect = JsonSchemaExpectation(runner, config)
        super().__init__(runner, config, [from_step, self._js_expect])

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "consume": {
                    "oneOf": [
                        {"type":"integer", "minimum": 0},
                        {"type":"string", "const":"all"},
                    ],
                    "default": "all",
                },
                "timeoutSeconds": {
                    "type": ["number", "null"],
                    "default": None,
                },
                "expect": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                        },
                    },
                },
            },
        }

    def check_json(self, json_data:dict[str,Any]):
        if not self._js_expect.check_against_json_schema(json_data):
            raise expectations.FailedTestStep("Message payload did not match the JSON schema")

    def check_topic(self, topic: str):
        if expected_topic := self._config.get('expect', dict()).get("topic"):
            if topic != expected_topic:
                raise expectations.ExpectationFailure("Received Topic", expected_topic, topic)

    def run(self):
        if (timeoutSeconds := self._config.get('timeoutSeconds', None)) is not None:
            timeout_time = datetime.now() + timedelta(seconds=timeoutSeconds)
        from_step = self.get_capability("FromStep").get_step()
        if json_multi := from_step.find_capability("JsonMultiContentCapability"):
            if expect := self._config.get('expect', dict()):
                if (expect_count := expect.get('count', None)) is not None:
                    while timeout_time is None or timeout_time > datetime.now():
                        if expect_count == json_multi.count:
                            break
                        else:
                            logger.debug("Waiting for message")
                            sleep(1)
                    else:
                        failure = expectations.ExpectationFailure("Message count", expect_count, json_multi.count)
                        raise failure
            consume_count = self._config.get('consume', 'all')
            if consume_count == 'all':
                consume_count = json_multi.count
            for _ in range(consume_count):
                json_content = json_multi.get_content()
                self.check_topic(json_content.properties.get("topic"))
                self.check_json(json_content.content)
        elif js_resp_bod := from_step.find_capability("JsonResponseBodyCapability"):
            self.check_json(js_resp_bod.json_content)
        else:
            raise expectations.TestStepMissingCapability("No JsonMultiContentCapability or JsonResponseBodyCapability capability found.")
