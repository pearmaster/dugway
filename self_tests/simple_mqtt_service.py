import paho.mqtt.client as mqtt
from paho.mqtt.enums import (
    CallbackAPIVersion,
    MQTTProtocolVersion,
)
from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.packettypes import PacketTypes

# MQTT broker details
broker_address = "localhost"
broker_port = 1883

# Topic names
subscribe_topic = "hello/ping"
publish_topic = "hello/pong"

# MQTT v5 client
client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, protocol=MQTTProtocolVersion.MQTTv5)

def on_connect(client, userdata, flags, reason_code, properties):
    if not reason_code.is_failure:
        print("Connected to MQTT broker")
        client.subscribe(subscribe_topic)
    else:
        print("Connection failed with result code " + str(reason_code))

def on_message(client, userdata, msg: mqtt.MQTTMessage):
    # Process the received message
    print(f"Received message: {msg.payload.decode()} {msg.properties}")

    response_topic = publish_topic
    if hasattr(msg.properties, 'ResponseTopic'):
        response_topic = msg.properties.ResponseTopic
    
    resp_props = mqtt.Properties(PacketTypes.PUBLISH)

    if cor_id := getattr(msg.properties, 'CorrelationData', False):
        resp_props.CorrelationData = cor_id

    if contType := getattr(msg.properties, 'ContentType', False):
        resp_props.ContentType = contType

    # Publish the new message
    client.publish(response_topic, msg.payload, qos=1, properties=resp_props)

client.on_connect = on_connect
client.on_message = on_message

client.connect(broker_address, broker_port)
client.loop_forever()