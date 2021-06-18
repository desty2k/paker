from .subpackage import is_even


class Fibonacci:

    def __init__(self):
        super(Fibonacci, self).__init__()

    @staticmethod
    def fib(n):
        fib_num = [0, 1]
        for i in range(2, n):
            fib_num.append(fib_num[i - 1] + fib_num[i - 2])
        return fib_num

    @staticmethod
    def fib_odd(n):
        return [num for num in Fibonacci.fib(n) if not is_even(num)]
