import sys
import handler

data = sys.stdin.read()
print(handler.handle(data), end="")
