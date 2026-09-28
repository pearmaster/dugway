from dugway.step import TestStep


class SourceStep(TestStep):
    """Stands in for an earlier step that provides the given capabilities."""

    def __init__(self, runner, capabilities):
        super().__init__(runner, {"type": "source"}, capabilities)

    def get_config_schema(self):
        return True

    def run(self):
        pass
