import sys
import heapq
from collections import deque,defaultdict

def func():
    input = sys.stdin.readline
    W,Q = map(int,input().split())

    q = deque()
    cnt = defaultdict(int)
    ver = defaultdict(int)
    heap=[]
    out=[]

    def update(word,delta):
        cnt[word]+=delta
        ver[word]+=1
        if cnt[word] == 0:
            del cnt[word]
        else:
            heapq.heappush(heap,(-cnt[word],word,ver[word]))
    for _ in range(Q):
        parts=input().split()
        if parts[0]=='add':
            word=parts[1]
            q.append(word)
            update(word,1)
            if len(q)>W:
                old=q.popleft()
                update(old,-1)
        else:
            k=int(parts[1])
            tmp=[]
            ans=[]
            while heap and len(ans)<k:
                negc,word,v=heapq.heappop(heap)
                if word in cnt and ver[word] == v and -negc == cnt[word]:
                    ans.append(word)
                    tmp.append((negc,word,v))
            for item in tmp:
                heapq.heappush(heap,item)
            out.append(' '.join(ans))
    sys.stdout.write('\n'.join(out))
if __name__ == "__main__":
    func()