"""Numeric window shortcuts that leave numbers available in text editors."""
from PyQt5 import QtCore, QtGui, QtWidgets


class NumericEditorFilter(QtCore.QObject):
    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.ShortcutOverride and QtCore.Qt.Key_0 <= event.key() <= QtCore.Qt.Key_9:
            focus = QtWidgets.QApplication.focusWidget()
            editing = isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QTextEdit,
                                         QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox))
            editing |= isinstance(focus, QtWidgets.QComboBox) and focus.isEditable()
            if editing and self.parent().isAncestorOf(focus):
                event.accept()
                return True
        return False


def install_numeric_editor_filter(window):
    window.numeric_editor_filter = NumericEditorFilter(window)
    QtWidgets.QApplication.instance().installEventFilter(window.numeric_editor_filter)


def bind_button_keys(window, button, *keys):
    button.key_shortcuts = []
    for key in keys:
        shortcut = QtWidgets.QShortcut(QtGui.QKeySequence(key), window)
        shortcut.setAutoRepeat(False)
        shortcut.activated.connect(button.click)
        button.key_shortcuts.append(shortcut)
