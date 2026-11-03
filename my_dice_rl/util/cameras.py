"""Ordered camera identities shared by capture, training and deployment."""
DUAL_CAMERAS = ('wrist', 'external')
TRIPLE_CAMERAS = ('wrist', 'external', 'side')


def camera_names(config=None):
    config = config or {}
    names = config.get('camera_names')
    if names is None and 'camera_keys' in config:
        names = [key.removeprefix('camera_') for key in config['camera_keys']]
    if names is None:
        names = TRIPLE_CAMERAS if config.get('side_topic') else DUAL_CAMERAS
    names = tuple(names)
    if names not in (DUAL_CAMERAS, TRIPLE_CAMERAS):
        raise ValueError('camera_names must be [wrist, external] or [wrist, external, side], in that order')
    return names


def camera_keys(config=None):
    return tuple('camera_' + name for name in camera_names(config))


def validate_camera_topics(config):
    names = camera_names(config)
    topics = [config.get(name + '_topic') for name in names]
    if any(not isinstance(topic, str) or not topic.startswith('/') for topic in topics):
        raise ValueError('Each configured camera requires an absolute ROS image topic')
    if len(set(topics)) != len(topics):
        raise ValueError('Camera topics must be distinct; a repeated image is not another view')
    return names


def matched_images(candidates, names, max_skew):
    """Freshest causal tuple with bounded total spread; avoids N-camera products."""
    # Each possible minimum timestamp defines a window. Descending windows
    # maximize freshness of the oldest camera, as the previous dual matcher did.
    starts = sorted({s.received for key in names for s in candidates[key]}, reverse=True)
    for start in starts:
        selected = {}
        for key in names:
            sample = next((s for s in reversed(candidates[key])
                           if start <= s.received <= start + max_skew + 1e-12), None)
            if sample is None:
                break
            selected[key] = sample
        if len(selected) == len(names):
            return selected
    return None
