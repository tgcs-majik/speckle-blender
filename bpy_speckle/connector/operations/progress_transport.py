from specklepy.transports.server import ServerTransport


class ProgressServerTransport(ServerTransport):
    """A ``ServerTransport`` that reports upload progress to a Blender window
    manager and the console.

    ``save_object`` is invoked on the main thread during serialization, so
    driving ``wm.progress_update`` from here is thread-safe. Any progress error
    is swallowed so it can never break a publish.

    ``wm`` is duck-typed (only ``progress_update`` is called) so this module has
    no hard ``bpy`` dependency and can be unit-tested without Blender.
    """

    def __init__(self, *args, wm=None, total=0, **kwargs):
        super().__init__(*args, **kwargs)
        self._wm = wm
        self._total = max(int(total), 1)
        self._n = 0

    @property
    def progress_count(self) -> int:
        """Number of objects passed to ``save_object`` so far."""
        return self._n

    def save_object(self, id: str, serialized_object: str) -> None:
        super().save_object(id, serialized_object)
        self._n += 1
        if self._wm is not None:
            try:
                self._wm.progress_update(min(99, int(self._n / self._total * 100)))
            except Exception:
                pass
        if self._n % 1000 == 0:
            print(f"[Speckle] Uploading objects: {self._n}/{self._total}")
