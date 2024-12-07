

class FailedTestStep(Exception):
    pass

class ExpectationFailure(FailedTestStep):
    
    def __init__(self, message, expected, actual):
        super().__init__(message)
        self.expected = expected
        self.actual = actual

class InvalidTestConfig(Exception):
    pass

class TestStepMissingCapability(InvalidTestConfig):
    pass