import json
import paker

# THIS STILL DOES NOT WORK

if __name__ == '__main__':
    p = paker.Paker()

    # serialize and write module to file
    serialized = p.dump("mss")
    with open("mss_example.dump", "w+") as f:
        f.write(json.dumps(serialized, indent=4))

    # now you can delete package directory
    # load package back from dump file
    with open("mss_example.dump", "r") as f:
        dumped = f.read()

    p.load(dumped)
    import mss
    print(dir(mss))
    print(mss.mss().grab(mss.mss().monitors[0]))
