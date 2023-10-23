
from typing import Callable, Any
import json

import paho.mqtt.client as mqtt_client
import paho.mqtt.properties as props
from paho.mqtt.packettypes import PacketTypes

from runner import Service, TestStep, TestRunner
from meta import JsonConfigType, JsonSchemaType
from capabilities import ServiceDependency, JsonResponseBodyCapability

class MqttService(Service):

    def __init__(self, config: JsonConfigType):
        super().__init__(config)
        kwargs = dict()
        if client_id := config.get('clientId', False):
            kwargs['client_id'] = client_id
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
            self.client.username_pw_set(credentials['username'], credentials['password'])
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
            self._config['hostname'],
            self._config.get('port', 1883),
            self._config.get('keepAlive', 60),
        ]
        kwargs = dict()
        if self.is_v5:
            prop_config = self._config.get('connectProperties', dict())
            connect_props = props.Properties(PacketTypes.CONNECT)
            if s_e_i := prop_config.get('sessionExpiryInterval', False) is not False:
                connect_props.SessionExpiryInterval = s_e_i
            if r_m := prop_config.get('receiveMaximum', False) is not False:
                connect_props.ReceiveMaximum = r_m
            if m_p_s := prop_config.get('maximumPacketSize', False) is not False:
                connect_props.MaximumPacketSize = m_p_s
            if len(prop_config) > 0:
                kwargs['properties'] = connect_props
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
        if self.is_v5 and properties is not None:
            self.client.publish(topic=topic, payload=payload, qos=qos, retain=retain, properties=properties)
        else:
            self.client.publish(topic=topic, payload=payload, qos=qos, retain=retain)
    
    def subscribe(self, sub_topic:str, qos:int, callback:Callable[[mqtt_client.Client,Any,str], None]):
        self.client.message_callback_add(sub_topic, callback)
        self._subscriptions.append(sub_topic)
        self.client.subscribe(sub_topic, qos)


class MqttPublish(TestStep):

    def __init__(self, runner: TestRunner, config: JsonConfigType):
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
                "publishProperties": {
                    "type": "object",
                    "properties": {
                        "payloadFormatIndicator": {"type": "integer", "minimum": 0, "maximum": 1},
                        "messageExpiryInterval": {"type": "integer"},
                        "responseTopic": {"type":"string"},
                        "correlationData": {"type":"string"},
                        "contentType": {"type":"string"},
                    },
                    "additionalProperties": False,
                }
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
        mqtt_service = self.get_capability("ServiceDependency").get_service()
        if mqtt_service.is_v5 and (pub_prop_config := self._config.get('publishProperties', False)):
            pub_props = props.Properties(PacketTypes.PUBLISH)
            if p_f_i := pub_prop_config.get('payloadFormatIndicator', False) is not False:
                pub_props.PayloadFormatIndicator = p_f_i
            if m_e_i := pub_prop_config.get('messageExpiryInterval', False) is not False:
                pub_props.MessageExpiryInterval = m_e_i
            if r_t := pub_prop_config.get('responseTopic', False) is not False:
                pub_props.ResponseTopic = r_t
            if c_t := pub_prop_config.get('contentType', False) is not False:
                pub_props.ContentType = c_t
            mqtt_service.publish(self._topic, self._qos, self._retain, pub_props)
        else:
            mqtt_service.publish(self._topic, self._qos, self._retain)

