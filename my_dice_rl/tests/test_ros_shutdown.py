"""Expected context shutdown stays quiet; active runtime faults remain visible."""
import sys

import pytest
import rclpy

from my_dice_rl.env.real import spacemouse,teleop
from my_dice_rl.env.real.ros_backend import spin_until_shutdown


@pytest.mark.parametrize('module',[spacemouse,teleop])
@pytest.mark.parametrize('context_alive',[False,True])
def test_context_shutdown_cleanup_does_not_hide_active_errors(monkeypatch,module,context_alive):
    calls=[]
    class Device:
        def __init__(self,*args,**kwargs):pass
        def close(self):calls.append('device_closed')
    class Node:
        def __init__(self,*args,**kwargs):pass
        def create_publisher(self,*args,**kwargs):return None
        def create_timer(self,*args,**kwargs):return None
        def _set_enabled(self,*args):calls.append('pause')
        def _control_step(self):calls.append('hold')
        def destroy_node(self):calls.append('node_destroyed')
    def spin(node):
        raise RuntimeError('simulated spin/context error')
    monkeypatch.setattr(sys,'argv',['spacemouse'])
    monkeypatch.setattr(rclpy,'init',lambda *a,**k:None)
    monkeypatch.setattr(rclpy,'ok',lambda:context_alive)
    monkeypatch.setattr(rclpy,'spin',spin)
    monkeypatch.setattr(rclpy,'try_shutdown',lambda:calls.append('context_closed'))
    monkeypatch.setattr(spacemouse,'LinuxSpaceMouse',Device)
    monkeypatch.setattr('rclpy.node.Node',Node)
    monkeypatch.setattr(teleop,'FrankaSpaceMouseTeleop',Node)
    if context_alive:
        with pytest.raises(RuntimeError,match='simulated spin'):
            module.main()
    else:
        module.main()
    assert 'node_destroyed' in calls and 'context_closed' in calls
    if module is spacemouse:
        assert 'device_closed' in calls


@pytest.mark.parametrize('context_alive',[False,True])
def test_background_spin_only_ignores_errors_after_context_shutdown(monkeypatch,context_alive):
    class Executor:
        def spin(self):raise RuntimeError('simulated executor error')
    monkeypatch.setattr(rclpy,'ok',lambda:context_alive)
    if context_alive:
        with pytest.raises(RuntimeError,match='simulated executor'):
            spin_until_shutdown(Executor())
    else:
        spin_until_shutdown(Executor())
