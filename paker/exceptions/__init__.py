

class PakerImportError(Exception):

    def __init__(self, message):
        super().__init__(message)


class PakerDumpError(Exception):

    def __init__(self, message):
        super().__init__(message)
