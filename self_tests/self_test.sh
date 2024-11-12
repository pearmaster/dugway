#!/bin/bash

mosquitto -d

python3 simple_mqtt_service.py &
