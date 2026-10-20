"""Test-only ComfyUI node that holds an image for a fixed time.

It is not part of the Anim Bridge package. Install it into a test ComfyUI
only to reproduce long-running executions without a GPU (see test/README.md).
"""
import time


class AnimBridgeTestDelay:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'image': ('IMAGE',),
                'seconds': ('INT', {'default': 60, 'min': 0, 'max': 600}),
            },
        }

    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'hold'
    CATEGORY = 'Anim/Test'
    DESCRIPTION = 'Test only: returns the input image after waiting the given seconds.'

    @classmethod
    def IS_CHANGED(cls, image, seconds):
        # Never reuse a cached result; every run must wait again.
        return float('nan')

    def hold(self, image, seconds):
        time.sleep(seconds)
        return (image,)


NODE_CLASS_MAPPINGS = {'AnimBridgeTestDelay': AnimBridgeTestDelay}
NODE_DISPLAY_NAME_MAPPINGS = {'AnimBridgeTestDelay': 'Anim Bridge Test Delay (test only)'}
