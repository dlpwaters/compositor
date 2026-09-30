"""Finite CPU jobs with a responsive native event loop."""

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout


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
    def reject(self):
        # A C kernel cannot safely be interrupted halfway through a raster write.
        pass

    def closeEvent(self, event):
        event.ignore()


def run_task(parent, label, function):
    dialog = BusyDialog(parent)
    dialog.setWindowTitle("Compositor")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(label))
    progress = QProgressBar()
    progress.setRange(0, 0)
    layout.addWidget(progress)
    dialog.setMinimumWidth(300)
    result, pool, response = Result(), QThreadPool(), []
    pool.setMaxThreadCount(1)

    def finished(value, error):
        response.extend([value, error])
        dialog.accept()

    result.ready.connect(finished, Qt.ConnectionType.QueuedConnection)
    pool.start(Job(function, result))
    try:
        dialog.exec()
        pool.waitForDone()
    finally:
        dialog.deleteLater()
    if response[1] is not None:
        raise response[1]
    return response[0]
