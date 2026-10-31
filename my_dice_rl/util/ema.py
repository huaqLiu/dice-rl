"""EMA inference copy; optimizer updates the original model only."""
import copy

import torch


class EMA:
    def __init__(self, model, decay=.995):
        if not 0 <= decay < 1:
            raise ValueError('EMA decay must be in [0,1)')
        self.decay = decay
        self.model = copy.deepcopy(model).eval().requires_grad_(False)
        self.updates = 0

    @torch.no_grad()
    def update(self, model):
        parameters = dict(model.named_parameters())
        for name, value in self.model.named_parameters():
            value.lerp_(parameters[name], 1-self.decay)
        buffers = dict(model.named_buffers())
        for name, value in self.model.named_buffers():
            value.copy_(buffers[name])
        self.updates += 1

    def state_dict(self):
        return dict(model=self.model.state_dict(), decay=self.decay, updates=self.updates)

    def load_state_dict(self, state):
        self.model.load_state_dict(state['model'])
        self.decay, self.updates = state['decay'], state['updates']
