import io
import paker
import logging


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    # serialize and write module to file
    serialized = paker.dump("mss")

    # now you can uninstall mss using `pip uninstall mss -y`
    # load package back from dump file
    with open("mss.zip", "rb") as f:
        zip_bytes = io.BytesIO(f.read())

    with paker.loads(zip_bytes.read()) as loader:
        loader.load_module("mss")
        import mss

        print(dir(mss))
        with mss.mss() as sct:
            sct.shot()
