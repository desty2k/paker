import paker
import logging

MODULE = '{"somemodule": {"type": "module", "extension": "py", "code": "fun = lambda x: x**2"}}'
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    with paker.loads(MODULE) as loader:
        # somemodule will be available only in this context
        from somemodule import fun
        assert fun(2), 4
        assert fun(5), 25
        print("6**2 is {}".format(fun(6)))
        print("It works!")
