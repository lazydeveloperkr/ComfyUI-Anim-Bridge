"""Opt-in, no-model Desktop smoke fixture. Never installed by the Bridge itself."""
import hashlib
import time
import torch

class AnimBridgeSmokeImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'prompt': ('STRING', {'forceInput': True}),
                             'delay': ('FLOAT', {'default': 0.0, 'min': 0.0, 'max': 10.0}),
                             'fail': ('BOOLEAN', {'default': False})}}
    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'generate'
    CATEGORY = 'Anim/Tests'
    def generate(self, prompt, delay, fail):
        if fail:
            raise ValueError('Intentional Anim smoke failure')
        time.sleep(delay)
        color = hashlib.sha256(prompt.encode()).digest()[:3]
        return (torch.tensor(list(color), dtype=torch.float32).view(1, 1, 1, 3).expand(1, 64, 64, 3) / 255,)

NODE_CLASS_MAPPINGS = {'AnimBridgeSmokeImage': AnimBridgeSmokeImage}
NODE_DISPLAY_NAME_MAPPINGS = {'AnimBridgeSmokeImage': 'Anim Bridge Test Image (No Model)'}
