list = [1, 2, 3]
t= iter( list)
def er (n):
    for i in range(n):
        yield i
x = er(5)


def printinfo(arg1, **vardict):
    "打印任何传入的参数"
    print("输出: ")
    print(arg1)
    print(vardict)

def dectorator(func):
    def warper(*args, **kwargs):
        print("调用开始")
        func(*args, **kwargs)
        print("调用结束")
        return
    return warper

@dectorator
def test(a, b):
    print(a + b)

from collections import deque
queue = deque()
queue.append(1)
queue.append(2)
queue.append(3)
queue.popleft()
print(queue)
