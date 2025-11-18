# dugway

**Dugway API testing framework**

Dugway is heavily inspired by Tavern.  Tavern is mature and Dugway is still under initial development.  The main difference is that Tavern is a little more structured and simple, where Dugway is a little more flexible and powerful (but perhaps at the expense of some simplicity).

When Dugway is feature complete, it will have these features:

* More flexibility.  It will have the ability to test more than just HTTP and MQTT, depending on plugins provided.  Out of the box it will come with a SSE-stream plugin.
* MQTTv5 support.  It will have the ability to ensure that received MQTT messages have specific MQTTv5 properties set to specific values.
* JSON Schema validation.  Assert as much or as little of the response JSON that you want, by providing a JSON Schema for validation.
* API Spec integration.  Hook into OpenAPI or AsyncAPI specs for automated assertions.
* Pytest integration.  Run Dugway and get reports through pytest invocation.

## Installation

This Python project is managed by [uv](https://github.com/astral-sh/uv) from Astral.

## Usage

Coming soon.

## License

Apache 2.0 License.