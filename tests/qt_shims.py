"""Minimal Qt shims for headless tests of the GUI modules.

The plugin's GUI layer imports ``qt.core`` (and ``qt.webengine``) at module
scope. calibre bundles a real Qt, but CI has neither calibre nor PyQt installed,
so these stand-ins are injected into ``sys.modules`` at conftest import time.

They are deliberately dumb: widgets store the values the dialog code sets and
expose the small surface the modules touch. Signals are real (``connect`` /
``emit``) so wiring can be asserted, and threads only record their arguments —
calling :meth:`run` is explicit in the tests.
"""

from __future__ import annotations

import sys
import types


class _Signal:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot, *_args) -> None:
        self._slots.append(slot)

    def disconnect(self, slot=None) -> None:
        if slot is None:
            self._slots.clear()
        elif slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args) -> None:
        for slot in list(self._slots):
            slot(*args)


class _SignalDescriptor:
    def __init__(self, *types_) -> None:
        self._types = types_
        self._name = ""

    def __set_name__(self, owner, name) -> None:
        self._name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        signals = obj.__dict__.setdefault("_signals", {})
        return signals.setdefault(self._name, _Signal())


def pyqtSignal(*types_):
    return _SignalDescriptor(*types_)


class _Any:
    """Permissive value: callable, attribute-friendly, falsy only when asked."""

    def __call__(self, *args, **kwargs):
        return _Any()

    def __getattr__(self, name):
        return _Any()

    def __or__(self, other):
        return _Any()

    def __repr__(self) -> str:
        return "<qt shim>"


class QSize:
    def __init__(self, width=0, height=0) -> None:
        if isinstance(width, QSize):
            width, height = width.width(), width.height()
        self._width, self._height = int(width), int(height)

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def __eq__(self, other) -> bool:
        return isinstance(other, QSize) and (self._width, self._height) == (
            other._width,
            other._height,
        )

    def __hash__(self) -> int:
        return hash((self._width, self._height))

    def __repr__(self) -> str:
        return f"QSize({self._width}, {self._height})"


class QByteArray:
    def __init__(self, data=b"") -> None:
        self._data = bytes(data)

    def data(self) -> bytes:
        return self._data


class QColor:
    def __init__(self, value=None) -> None:
        self.value = value

    def name(self) -> str:
        return str(self.value)

    def lightness(self) -> int:
        return 255


class QBrush:
    def __init__(self, color=None) -> None:
        self.color = color


class QPen:
    def __init__(self, color=None) -> None:
        self.color = color
        self._width = 1

    def setWidth(self, width) -> None:
        self._width = width

    def width(self) -> int:
        return self._width

    def setCapStyle(self, _style) -> None:
        pass


class QPixmap:
    def __init__(self, size=None) -> None:
        self._size = size if isinstance(size, QSize) else QSize()
        self._null = size is None

    def loadFromData(self, _data) -> bool:
        self._null = False
        return True

    def isNull(self) -> bool:
        return self._null

    def size(self) -> QSize:
        return self._size

    def scaled(self, size, *_args):
        return QPixmap(size)

    def fill(self, _color) -> None:
        self._null = False


class QIcon:
    def __init__(self, _path=None) -> None:
        self._null = _path is None

    def isNull(self) -> bool:
        return self._null


class QFont:
    def __init__(self) -> None:
        self._bold = False
        self._size = 10

    def setBold(self, bold) -> None:
        self._bold = bold

    def bold(self) -> bool:
        return self._bold

    def setPointSize(self, size) -> None:
        self._size = size

    def pointSize(self) -> int:
        return self._size


class _Rect:
    def adjusted(self, *_args):
        return self

    def width(self) -> int:
        return 100

    def height(self) -> int:
        return 100


class _Widget:
    def __init__(self, parent=None, *args, **kwargs) -> None:
        self.parent = parent
        self._text = ""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _noop(*args, **kwargs):
            return _Any()

        return _noop

    def setObjectName(self, name) -> None:
        self._object_name = name

    def setWindowTitle(self, title) -> None:
        self._window_title = title

    def windowTitle(self) -> str:
        return getattr(self, "_window_title", "")

    def setWindowFlags(self, flags):
        self._window_flags = flags

    def windowFlags(self):
        return getattr(self, "_window_flags", 0)

    def setWindowState(self, state) -> None:
        self._window_state = state

    def windowState(self):
        return getattr(self, "_window_state", 0)

    def isMaximized(self) -> bool:
        return False

    def saveGeometry(self):
        return QByteArray(b"geometry")

    def restoreGeometry(self, _data) -> None:
        pass

    def resize(self, width, height=None) -> None:
        self._size = (width, height)

    def size(self) -> QSize:
        width, height = getattr(self, "_size", (0, 0))
        return QSize(width, height or 0)

    def setFixedSize(self, width, height=None) -> None:
        self._fixed = (width, height)

    def setMinimumSize(self, width, height=None) -> None:
        self._minimum = (width, height)

    def setMinimumWidth(self, width) -> None:
        self._minimum_width = width

    def setStyleSheet(self, style) -> None:
        self._style = style

    def styleSheet(self) -> str:
        return getattr(self, "_style", "")

    def setWordWrap(self, wrap) -> None:
        self._word_wrap = wrap

    def setAlignment(self, alignment) -> None:
        self._alignment = alignment

    def font(self) -> QFont:
        if "_font" not in self.__dict__:
            self._font = QFont()
        return self._font

    def setFont(self, font) -> None:
        self._font = font

    def setText(self, text) -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setPixmap(self, pixmap) -> None:
        self._pixmap = pixmap

    def pixmap(self):
        return getattr(self, "_pixmap", None)

    def palette(self):
        return QPalette()

    def rect(self) -> _Rect:
        return _Rect()

    def width(self) -> int:
        return 100

    def height(self) -> int:
        return 100

    def setContentsMargins(self, *_args) -> None:
        pass

    def setToolTip(self, _text) -> None:
        pass

    def setTextInteractionFlags(self, _flags) -> None:
        pass

    def setCursor(self, _cursor) -> None:
        pass

    def setFrameShape(self, _shape) -> None:
        pass

    def show(self) -> None:
        self._shown = True

    def hide(self) -> None:
        self._shown = False

    def setVisible(self, visible) -> None:
        self._shown = bool(visible)

    def isVisible(self) -> bool:
        return bool(getattr(self, "_shown", False))

    def update(self) -> None:
        pass

    def updateGeometry(self) -> None:
        pass

    def setEnabled(self, enabled) -> None:
        self._enabled = bool(enabled)

    def isEnabled(self) -> bool:
        return getattr(self, "_enabled", True)

    def setTabOrder(self, *_args) -> None:
        pass

    def mousePressEvent(self, _event) -> None:
        pass

    def mouseReleaseEvent(self, _event) -> None:
        pass

    def mouseDoubleClickEvent(self, _event) -> None:
        pass

    def keyPressEvent(self, _event) -> None:
        pass

    def paintEvent(self, _event) -> None:
        pass

    def showEvent(self, _event) -> None:
        pass

    def hideEvent(self, _event) -> None:
        pass

    def closeEvent(self, _event) -> None:
        pass

    def resizeEvent(self, _event) -> None:
        pass

    def changeEvent(self, _event) -> None:
        pass

    def sizeHint(self) -> QSize:
        return QSize(0, 0)

    def accept(self) -> None:
        self._accepted = True

    def reject(self) -> None:
        self._rejected = True


class QWidget(_Widget):
    pass


class QFrame(QWidget):
    class Shape:
        NoFrame = 0


class QLabel(QWidget):
    def __init__(self, text="", parent=None) -> None:
        if not isinstance(text, str):
            parent, text = text, ""
        super().__init__(parent)
        self._text = text


class QProgressBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 0
        self._value = 0

    def setRange(self, minimum, maximum) -> None:
        self._minimum, self._maximum = int(minimum), int(maximum)

    def range(self) -> tuple[int, int]:
        return self._minimum, self._maximum

    def setValue(self, value) -> None:
        self._value = max(self._minimum, min(self._maximum, int(value)))

    def value(self) -> int:
        return self._value


class QSpinBox(QWidget):
    valueChanged = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 99
        self._value = 0

    def setRange(self, minimum, maximum) -> None:
        self._minimum, self._maximum = int(minimum), int(maximum)

    def setValue(self, value) -> None:
        self._value = max(self._minimum, min(self._maximum, int(value)))

    def value(self) -> int:
        return self._value

    def setSuffix(self, _suffix) -> None:
        pass


class QPushButton(QWidget):
    clicked = pyqtSignal(bool)

    def __init__(self, text="", parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._enabled = True

    def click(self) -> None:
        """Emit ``clicked`` like Qt does for a keyboard/mouse activation."""
        self.clicked.emit()


class QCheckBox(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, text="", parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._checked = False

    def setChecked(self, checked) -> None:
        self._checked = bool(checked)

    def isChecked(self) -> bool:
        return self._checked


class QLineEdit(QWidget):
    returnPressed = pyqtSignal()
    textChanged = pyqtSignal(str)

    class EchoMode:
        Normal = 0
        Password = 1

    def __init__(self, text="", parent=None) -> None:
        if not isinstance(text, str):
            parent, text = text, ""
        super().__init__(parent)
        self._text = text

    def setPlaceholderText(self, _text) -> None:
        pass

    def setText(self, text) -> None:
        self._text = text
        self.textChanged.emit(str(text))


class QComboBox(QWidget):
    currentIndexChanged = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, object]] = []
        self._index = -1

    def clear(self) -> None:
        self._items.clear()
        self._index = -1

    def addItem(self, text, data=None) -> None:
        self._items.append((text, data))
        if self._index < 0:
            self._index = 0

    def currentIndex(self) -> int:
        return self._index

    def currentData(self):
        if 0 <= self._index < len(self._items):
            return self._items[self._index][1]
        return None

    def currentText(self) -> str:
        if 0 <= self._index < len(self._items):
            return self._items[self._index][0]
        return ""

    def count(self) -> int:
        return len(self._items)

    def itemText(self, index: int) -> str:
        if 0 <= index < len(self._items):
            return self._items[index][0]
        return ""

    def itemData(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index][1]
        return None

    def setCurrentIndex(self, index) -> None:
        changed = self._index != index
        self._index = index
        if changed:
            self.currentIndexChanged.emit(index)

    def findData(self, data):
        for index, (_text, value) in enumerate(self._items):
            if value == data:
                return index
        return -1


class QTreeWidgetItem:
    def __init__(self, strings=None, parent=None) -> None:
        self._texts = list(strings or [])
        self._data: dict = {}
        self._children: list[QTreeWidgetItem] = []
        self._parent = None
        self._flags = 0
        self._check_states: dict[int, int] = {}
        if isinstance(parent, QTreeWidget):
            parent.addTopLevelItem(self)
        elif isinstance(parent, QTreeWidgetItem):
            parent.addChild(self)

    def setText(self, column, text) -> None:
        while len(self._texts) <= column:
            self._texts.append("")
        self._texts[column] = str(text)

    def text(self, column) -> str:
        return self._texts[column] if 0 <= column < len(self._texts) else ""

    def setData(self, column, role, value) -> None:
        self._data[(column, role)] = value

    def data(self, column, role):
        return self._data.get((column, role))

    def setFlags(self, flags) -> None:
        self._flags = flags

    def flags(self):
        return self._flags

    def setCheckState(self, column, state) -> None:
        self._check_states[column] = state

    def checkState(self, column):
        return self._check_states.get(column, 0)

    def setToolTip(self, column, text) -> None:
        self._data[(column, "tooltip")] = str(text)

    def addChild(self, child) -> None:
        child._parent = self
        self._children.append(child)

    def childCount(self) -> int:
        return len(self._children)

    def child(self, index):
        return self._children[index] if 0 <= index < len(self._children) else None

    def takeChild(self, index):
        if 0 <= index < len(self._children):
            child = self._children.pop(index)
            child._parent = None
            return child
        return None

    def setExpanded(self, expanded) -> None:
        self._expanded = bool(expanded)

    def isExpanded(self) -> bool:
        return bool(getattr(self, "_expanded", False))


class QTreeWidget(QWidget):
    itemExpanded = pyqtSignal(object)
    currentItemChanged = pyqtSignal(object, object)
    itemDoubleClicked = pyqtSignal(object, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[QTreeWidgetItem] = []
        self._current: QTreeWidgetItem | None = None
        self._headers: list[str] = []

    def setHeaderLabels(self, labels) -> None:
        self._headers = [str(label) for label in labels]

    def addTopLevelItem(self, item) -> None:
        self._items.append(item)

    def topLevelItemCount(self) -> int:
        return len(self._items)

    def topLevelItem(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def clear(self) -> None:
        self._items.clear()
        self._current = None

    def currentItem(self):
        return self._current

    def setCurrentItem(self, item) -> None:
        previous = self._current
        self._current = item
        if previous is not item:
            self.currentItemChanged.emit(item, previous)

    def expandItem(self, item) -> None:
        item.setExpanded(True)
        self.itemExpanded.emit(item)


class QAction(QWidget):
    triggered = pyqtSignal(bool)

    def __init__(self, text="", parent=None) -> None:
        super().__init__(parent)
        self._text = text

    def setMenu(self, menu) -> None:
        self._menu = menu

    def menu(self):
        return getattr(self, "_menu", None)


class QMenu(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._actions: list[tuple[str, object]] = []

    def addAction(self, text, slot=None):
        self._actions.append((text, slot))
        return QAction(text, self)

    def actions(self):
        return list(self._actions)


class QFileDialog:
    @staticmethod
    def getExistingDirectory(*_args, **_kwargs) -> str:
        return ""


class _Layout:
    def __init__(self, parent=None) -> None:
        self.parent = parent
        self._items: list = []
        self._rows: list = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _noop(*args, **kwargs):
            return _Any()

        return _noop

    def addWidget(self, widget, *args) -> None:
        self._items.append(widget)

    def addLayout(self, layout, *args) -> None:
        self._items.append(layout)

    def addStretch(self, *args) -> None:
        pass

    def setContentsMargins(self, *_args) -> None:
        pass

    def setSpacing(self, _spacing) -> None:
        pass

    def setStretchFactor(self, *_args) -> None:
        pass

    def setSizes(self, *_args) -> None:
        pass


class QVBoxLayout(_Layout):
    pass


class QHBoxLayout(_Layout):
    pass


class QFormLayout(_Layout):
    class FieldGrowthPolicy:
        AllNonFixedFieldsGrow = 0

    def addRow(self, label, field=None) -> None:
        self._rows.append((label, field))

    def rowCount(self) -> int:
        return len(self._rows)

    def removeRow(self, index) -> None:
        del self._rows[index]


class QListWidgetItem:
    def __init__(self) -> None:
        self._data: dict = {}
        self._size = QSize()
        self._flags = None
        self._background = None

    def setData(self, role, value) -> None:
        self._data[role] = value

    def data(self, role):
        return self._data.get(role)

    def setFlags(self, flags) -> None:
        self._flags = flags

    def setSizeHint(self, size) -> None:
        self._size = size

    def setBackground(self, brush) -> None:
        self._background = brush


class QListWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[QListWidgetItem] = []
        self._widgets: dict[int, QWidget] = {}
        self._signals_blocked = False

    def blockSignals(self, blocked) -> None:
        self._signals_blocked = blocked

    def clear(self) -> None:
        self._items.clear()
        self._widgets.clear()

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def item(self, index):
        return self._items[index]

    def setItemWidget(self, item, widget) -> None:
        self._widgets[id(item)] = widget

    def itemWidget(self, item):
        return self._widgets.get(id(item))


class _TabBar(_Widget):
    def sizeHint(self) -> QSize:
        return QSize(120, 32)


class QTabWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tabs: list[tuple[QWidget, str]] = []
        self._bar = _TabBar(self)

    def tabBar(self):
        return self._bar

    def addTab(self, widget, icon, label) -> None:
        self._tabs.append((widget, label))

    def indexOf(self, widget) -> int:
        for index, (candidate, _label) in enumerate(self._tabs):
            if candidate is widget:
                return index
        return -1

    def setTabText(self, index, text) -> None:
        if 0 <= index < len(self._tabs):
            widget, _label = self._tabs[index]
            self._tabs[index] = (widget, text)

    def tabText(self, index) -> str:
        return self._tabs[index][1]

    def count(self) -> int:
        return len(self._tabs)

    def setCornerWidget(self, widget, _corner) -> None:
        self._corner = widget


class QSplitter(QWidget):
    class Orientation:
        Horizontal = 0
        Vertical = 1

    def addWidget(self, widget) -> None:
        pass


class QScrollArea(QWidget):
    def setWidgetResizable(self, _resizable) -> None:
        pass

    def setWidget(self, widget) -> None:
        self._widget = widget


class QTimer:
    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.timeout = _Signal()
        self._interval = 0
        self._active = False

    def setInterval(self, interval) -> None:
        self._interval = interval

    def setSingleShot(self, _single_shot) -> None:
        pass

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    def isActive(self) -> bool:
        return self._active

    @staticmethod
    def singleShot(_msecs, callback) -> None:
        callback()


class QThread:
    finished = pyqtSignal()

    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.started = False
        self._interrupted = False

    def start(self) -> None:
        self.started = True

    def wait(self, _msecs=None) -> bool:
        return True

    def requestInterruption(self) -> None:
        self._interrupted = True

    def isInterruptionRequested(self) -> bool:
        return self._interrupted

    @staticmethod
    def currentThread():
        return _Any()


class QObject:
    def __init__(self, parent=None) -> None:
        self.parent = parent

    def thread(self):
        return None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _noop(*args, **kwargs):
            return _Any()

        return _noop


class QFileSystemWatcher(QObject):
    directoryChanged = pyqtSignal(str)
    fileChanged = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._paths: set[str] = set()

    def addPath(self, path) -> bool:
        self._paths.add(str(path))
        return True

    def removePaths(self, paths) -> None:
        for path in paths:
            self._paths.discard(str(path))

    def paths(self):
        return sorted(self._paths)


class QDialog(QWidget):
    class DialogCode:
        Accepted = 1
        Rejected = 0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result = 0

    def exec(self) -> int:
        return self._result

    def done(self, result) -> None:
        self._result = result

    def accept(self) -> None:
        self._accepted = True
        self._result = self.DialogCode.Accepted

    def reject(self) -> None:
        self._rejected = True
        self._result = self.DialogCode.Rejected

    def setWindowModality(self, _modality) -> None:
        pass


class QDialogButtonBox(QWidget):
    accepted = pyqtSignal()
    rejected = pyqtSignal()

    class StandardButton:
        Ok = 1
        Cancel = 2


class QMessageBox:
    @staticmethod
    def information(*_args, **_kwargs) -> None:
        pass

    @staticmethod
    def warning(*_args, **_kwargs) -> None:
        pass

    @staticmethod
    def question(*_args, **_kwargs):
        return QMessageBox.StandardButton.No

    class StandardButton:
        Yes = 1
        No = 0


class QInputDialog:
    @staticmethod
    def getText(*_args, **_kwargs):
        return "", False


class QUrl:
    def __init__(self, url="") -> None:
        self._url = str(url)

    def toString(self) -> str:
        return self._url

    def __str__(self) -> str:
        return self._url


class QPainter:
    class RenderHint:
        Antialiasing = 0

    def __init__(self, _widget=None) -> None:
        pass

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _noop(*args, **kwargs):
            return _Any()

        return _noop


class QPalette:
    class ColorRole:
        Highlight = 0
        Window = 1

    def color(self, _role) -> QColor:
        return QColor("white")


class _Screen:
    def availableGeometry(self):
        return _Rect()


class QApplication:
    @staticmethod
    def instance():
        return None

    @staticmethod
    def primaryScreen():
        return None


class _Enums:
    def __init__(self, **values) -> None:
        for key, value in values.items():
            setattr(self, key, value)


class Qt:
    Orientation = _Enums(Horizontal=1, Vertical=2)
    Corner = _Enums(TopRightCorner=1)
    AlignmentFlag = _Enums(AlignCenter=1, AlignTop=2, AlignHCenter=4, AlignRight=8)
    AspectRatioMode = _Enums(KeepAspectRatio=1)
    TransformationMode = _Enums(SmoothTransformation=1)
    GlobalColor = _Enums(lightGray=1)
    ItemDataRole = _Enums(UserRole=0x0100)
    ItemFlag = _Enums(ItemIsEnabled=1, ItemIsSelectable=2, ItemIsUserCheckable=4)
    CheckState = _Enums(Unchecked=0, PartiallyChecked=1, Checked=2)
    CursorShape = _Enums(PointingHandCursor=1)
    PenCapStyle = _Enums(RoundCap=1)
    TextInteractionFlag = _Enums(TextSelectableByMouse=1)
    ConnectionType = _Enums(QueuedConnection=1, BlockingQueuedConnection=2)
    WindowModality = _Enums(ApplicationModal=1)
    WindowType = _Enums(WindowMaximizeButtonHint=1, WindowMinimizeButtonHint=2)
    WindowState = _Enums(WindowMaximized=1)


class QAbstractItemView:
    class SelectionMode:
        NoSelection = 0


class QListView:
    class ViewMode:
        IconMode = 0

    class ResizeMode:
        Adjust = 0

    class Movement:
        Static = 0


class QWebEnginePage:
    def __init__(self, profile=None, window=None) -> None:
        self.profile = profile
        self.window = window
        self.matcher = None
        self.loaded: list[str] = []

    def acceptNavigationRequest(self, url, _type, _is_main_frame) -> bool:
        if self.matcher is not None and self.matcher(url.toString()):
            return False
        return True

    def parent(self):
        return self.window

    def load(self, url) -> None:
        self.loaded.append(str(url))


class QWebEngineProfile:
    def __init__(self, _parent=None) -> None:
        pass


class QWebEngineView(QWidget):
    def setPage(self, page) -> None:
        self.page = page


def install_fake_qt() -> None:
    """Idempotently install fake ``qt.core`` / ``qt.webengine`` modules."""
    if "qt.core" in sys.modules:
        return

    core = types.ModuleType("qt.core")
    exports = {
        name: value
        for name, value in globals().items()
        if name.startswith("Q") or name in {"Qt", "pyqtSignal"}
    }
    for name, value in exports.items():
        setattr(core, name, value)

    webengine = types.ModuleType("qt.webengine")
    webengine.QWebEnginePage = QWebEnginePage
    webengine.QWebEngineProfile = QWebEngineProfile
    webengine.QWebEngineView = QWebEngineView

    qt = types.ModuleType("qt")
    qt.core = core
    qt.webengine = webengine

    sys.modules.setdefault("qt", qt)
    sys.modules.setdefault("qt.core", core)
    sys.modules.setdefault("qt.webengine", webengine)
