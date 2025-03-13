

class FailedTestStep(Exception):
    pass

class ExpectationFailure(FailedTestStep):
    
    def __init__(self, message, expected, actual):
        super().__init__(message)
        self.expected = expected
        self.actual = actual

    def details(self) -> str:
        return f"Expected {self.expected} \nActual {self.actual}"

class InvalidTestConfig(Exception):
    pass

class TestStepMissingCapability(InvalidTestConfig):
    pass