from types import SimpleNamespace

import paho.mqtt.client as mqtt_client
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from dugway.step import TestStep


class SourceStep(TestStep):
    """Stands in for an earlier step that provides the given capabilities."""

    def __init__(self, runner, capabilities):
        super().__init__(runner, {"type": "source"}, capabilities)

    def get_object_schema(self):
        return True

    def run(self):
        pass


class FakeMqttClient(SimpleNamespace):
    """Stands in for paho's client, acknowledging requests the way a broker would."""

    def __init__(self, service, connack="Success", suback="Granted QoS 0"):
        super().__init__(connects=[], subscribes=[], publishes=[])
        self._service = service
        self._connack = connack
        self._suback = suback

    def connect(self, *args, **kwargs):
        self.connects.append(kwargs)

    def loop_start(self):
        if self._connack is not None:
            reason = ReasonCode(PacketTypes.CONNACK, self._connack)
            self._service._on_connect(self, None, None, reason, None)

    def message_callback_add(self, topic, callback):
        pass

    def subscribe(self, topic, qos):
        mid = len(self.subscribes) + 1
        self.subscribes.append(topic)
        if self._suback is not None:
            reason = ReasonCode(PacketTypes.SUBACK, self._suback)
            self._service._on_subscribe(self, None, mid, [reason], None)
        return mqtt_client.MQTT_ERR_SUCCESS, mid

    def publish(self, **kwargs):
        self.publishes.append(kwargs)
