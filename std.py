import time
import study
def dectorator(func):
    def warper(*args, **kwargs):
        pwd = input("输入密码:")
        start = time.time()
        if pwd == "123":
            print("密码正确")
            func(*args, **kwargs)
        else:
            print("密码错误")
        print("调用结束")
        end = time.time()
        print("耗时:", end - start)
        return
    return warper

@dectorator
def test(a, b):
    print(a + b)

test(1,2)
