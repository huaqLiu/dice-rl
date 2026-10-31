"""同机 learner/执行器的非阻塞进程锁；持锁进程退出后由操作系统自动释放。"""
import fcntl
from pathlib import Path


class DeviceLease:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def acquire(self):
        if self.stream is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+')
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            return False
        self.stream = stream
        return True

    def release(self):
        if self.stream is not None:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError('GPU 正由训练或执行进程使用，请等待其释放后重试')
        return self

    def __exit__(self, *args):
        self.release()
