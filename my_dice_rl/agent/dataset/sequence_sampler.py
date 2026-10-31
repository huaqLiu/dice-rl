"""History ends at t; action targets start at t; windows never cross episodes."""
import numpy as np


class SequenceSampler:
    def __init__(self, episode_ends, episode_ids, obs_steps, horizon_steps, stride=1):
        ends = np.asarray(episode_ends, dtype=np.int64)
        if (ends.ndim != 1 or not len(ends) or ends[0] <= 0
                or np.any(np.diff(ends) <= 0)):
            raise ValueError('episode_ends must be strictly increasing')
        if min(obs_steps, horizon_steps, stride) < 1:
            raise ValueError('Observation/action windows and stride must be positive')
        self.starts = np.r_[0, ends[:-1]]
        self.ends = ends
        self.obs_steps, self.horizon_steps = obs_steps, horizon_steps
        self.indices = []
        for episode in episode_ids:
            if not 0 <= episode < len(ends):
                raise ValueError('Episode index out of range')
            self.indices.extend((int(episode), t) for t in range(
                int(self.starts[episode]), int(ends[episode]), stride))

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, index):
        episode, anchor = self.indices[index]
        start, end = self.starts[episode], self.ends[episode]
        obs = np.clip(np.arange(anchor-self.obs_steps+1, anchor+1), start, end-1)
        future = np.arange(anchor, anchor+self.horizon_steps)
        return episode, anchor, obs, np.minimum(future, end-1), future < end
