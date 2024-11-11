
class ExpectationFailure(Exception):
    pass

class InvalidTestConfig(Exception):
    pass
class TestStepMissingCapability(InvalidTestConfig):
    pass