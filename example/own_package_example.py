import json
import paker

if __name__ == '__main__':
    p = paker.Paker()

    # serialize and write module to file
    serialized = p.dump("my_own_package", keep_paths=False)
    with open("my_own_package.dump", "w+") as f:
        f.write(json.dumps(serialized, indent=4))

    # now you can delete package directory
    # load package back from dump file
    with open("my_own_package.dump", "r") as f:
        dumped = f.read()

    # load method returns loaded modules
    deserialized = p.load(dumped)
    print("Deserialized package is <{} - {}>".format(deserialized.__name__, deserialized.__file__))
    print(deserialized.module_a.SomeClassFromModuleA.attr1)

    # or you can just import it
    import my_own_package
    print("After importing: {}".format(my_own_package))
