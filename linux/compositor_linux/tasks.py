"""Finite CPU jobs with a responsive native event loop."""

from threading import Event

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout


class Result(QObject):
    ready = Signal(object, object)


class Job(QRunnable):
    def __init__(self, function, result):
        super().__init__()
        self.function, self.result = function, result

    def run(self):
        try:
            value = self.function()
        except Exception as exc:
            self.result.ready.emit(None, exc)
        else:
            self.result.ready.emit(value, None)


class BusyDialog(QDialog):
    def __init__(self, parent=None, cancel_event=None):
        super().__init__(parent)
        self.cancel_event = cancel_event

    def reject(self):
        # The worker checks between bounded strips; let it exit before closing.
        if self.cancel_event is not None:
            self.cancel_event.set()

    def closeEvent(self, event):
        event.ignore()


def run_task(parent, label, function, cancellable=False):
    cancel_event = Event() if cancellable else None
    dialog = BusyDialog(parent, cancel_event)
    dialog.setWindowTitle("Compositor")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(label))
    progress = QProgressBar()
    progress.setRange(0, 0)
    layout.addWidget(progress)
    if cancellable:
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        layout.addWidget(cancel_button)
    dialog.setMinimumWidth(300)
    result, pool, response = Result(), QThreadPool(), []
    pool.setMaxThreadCount(1)

    def finished(value, error):
        response.extend([value, error])
        dialog.accept()

    result.ready.connect(finished, Qt.ConnectionType.QueuedConnection)
    pool.start(Job(lambda: function(cancel_event) if cancellable else function(), result))
    try:
        dialog.exec()
        pool.waitForDone()
    finally:
        dialog.deleteLater()
    if cancel_event is not None and cancel_event.is_set():
        return None
    if response[1] is not None:
        raise response[1]
    return response[0]
