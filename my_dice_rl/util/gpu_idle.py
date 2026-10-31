"""Read NVIDIA process/utilization state without importing Torch or allocating CUDA."""
import csv
import subprocess


def read_gpu(index='0'):
    def query(kind, fields, selected=False):
        command = ['nvidia-smi', '--query-'+kind+'='+fields, '--format=csv,noheader,nounits']
        if selected:
            command.append('--id='+str(index))
        output = subprocess.check_output(command, text=True, timeout=8)
        return [row for row in csv.reader(output.splitlines(), skipinitialspace=True) if row]
    rows = query('gpu', 'uuid,utilization.gpu,memory.used,memory.total', True)
    if len(rows) != 1:
        raise ValueError('Expected one selected NVIDIA GPU')
    uuid, utilization, used, total = rows[0]
    processes = query('compute-apps', 'gpu_uuid,pid,process_name,used_memory')
    return dict(uuid=uuid, utilization=float(utilization), used_mib=float(used), total_mib=float(total),
        processes=[dict(pid=int(row[1]), name=row[2], used_mib=row[3])
                   for row in processes if row[0] == uuid])


class IdleWindow:
    def __init__(self, seconds=60., max_utilization=5., max_used_mib=1024.):
        self.seconds = seconds
        self.max_utilization = max_utilization
        self.max_used_mib = max_used_mib
        self.since = None

    def update(self, sample, now):
        eligible = (not sample['processes'] and sample['utilization'] <= self.max_utilization
                    and sample['used_mib'] <= self.max_used_mib)
        if not eligible:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        return now-self.since >= self.seconds
