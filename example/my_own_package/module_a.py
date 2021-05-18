
class SomeClassFromModuleA:
    attr1 = "I am attr"
    attr2 = lambda: print("I am lambda")

    def __init__(self):
        super(SomeClassFromModuleA, self).__init__()
