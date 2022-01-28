import paker
import logging

# paker.loads accepts dict, str, bytes and bytearray objects
POW = {"pow": {"type": "module", "extension": "py", "code": "pow = lambda x, y: x**y"}}
SQR = '{"sqr": {"type": "module", "extension": "py", "code": "from pow import pow\\nsqr = lambda x: pow(x, 2)"}}'
TRI = b'{"tri": {"type": "module", "extension": "py", "code": "from pow import pow\\ntri = lambda x: pow(x, 3)"}}'

logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    # you can use nested loaders
    with paker.loads(POW) as pow_loader:
        with paker.loads(SQR) as sqr_loader:
            # pow and sqr will be available only in this context
            from sqr import sqr
            assert sqr(2), 4
            assert sqr(5), 25
            print("6**2 is {}".format(sqr(6)))

        with paker.loads(TRI) as tri_loader:
            from tri import tri
            assert tri(2), 8
            assert tri(5), 125
            print("6**3 is {}".format(tri(6)))
        print("It works!")
