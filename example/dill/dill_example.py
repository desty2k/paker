"""
Example for importing dill from JSON file using paker.
If you have dill installed you should remove it by using `pip uninstall dill -y` command.
Note that `dill.tests` package was removed before dumping dill to .json.
"""

import paker


class SerializableClass:
    pass


if __name__ == '__main__':
    with paker.load(open("dill.json", "r")) as loader:
        # dill can be imported only in this context
        import dill

        # create class instance
        obj = SerializableClass()
        setattr(obj, "attr", "This is string.")

        dilled = dill.dumps(obj)
        print("Serialized object is {}".format(dilled))
        print("Deserialized object is {}".format(dill.loads(dilled)))

    # import will throw error
    try:
        import dill
    except ImportError as f:
        print("dill unloaded successfullly!")
