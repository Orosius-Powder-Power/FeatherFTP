from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, QModelIndex, QObject, QPoint, Qt, Signal, Slot
from PySide6.QtGui import QAction, QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..errors import FtpError
from ..models import FtpConnectionConfig, FtpEntry, FtpEntryType, TransferKind, TransferStatus
from ..protocol import FtpSession
from ..store import Site, SiteStore
from ..transfer import TransferManager, TransferTask


class UiBridge(QObject):
    log_received = Signal(str, str)
    task_changed = Signal(object)
    connection_finished = Signal(object, object, object)
    remote_list_finished = Signal(object, object, object)


@dataclass(slots=True)
class LocalEntry:
    name: str
    path: Path
    is_dir: bool
    size: int | None
    modified: datetime | None
    is_parent: bool = False


class FileTableWidget(QTableWidget):
    files_dropped = Signal(str)

    def __init__(self, panel_name: str) -> None:
        super().__init__(0, 0)
        self.panel_name = panel_name
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDragDropMode(QAbstractItemView.DragDrop)

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt override
        if not self.selectionModel().selectedRows():
            return
        mime = QMimeData()
        mime.setData("application/x-featherftp-panel", self.panel_name.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._accepts_panel_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._accepts_panel_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._accepts_panel_drop(event):
            event.ignore()
            return
        source_panel = bytes(event.mimeData().data("application/x-featherftp-panel")).decode("utf-8")
        self.files_dropped.emit(source_panel)
        event.acceptProposedAction()

    def _accepts_panel_drop(self, event) -> bool:
        if not event.mimeData().hasFormat("application/x-featherftp-panel"):
            return False
        source_panel = bytes(event.mimeData().data("application/x-featherftp-panel")).decode("utf-8")
        return source_panel != self.panel_name


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Socket FTP Client")
        self.resize(1360, 820)
        self.setMinimumSize(1120, 680)

        self.bridge = UiBridge()
        self.bridge.log_received.connect(self.append_log)
        self.bridge.task_changed.connect(self.update_task_row)
        self.bridge.connection_finished.connect(self.on_connection_finished)
        self.bridge.remote_list_finished.connect(self.on_remote_list_finished)

        self.store = SiteStore()
        self.session: FtpSession | None = None
        self.active_config: FtpConnectionConfig | None = None
        self.remote_entries: list[FtpEntry] = []
        self.local_entries: list[LocalEntry] = []
        self.current_local_path = Path.home()
        self.task_rows: dict[int, int] = {}
        self.dark_theme = False
        self.remote_busy = False
        self._cursor_busy = False

        self.transfer_manager = TransferManager(
            config_provider=lambda: self.active_config,
            log_callback=lambda direction, text: self.bridge.log_received.emit(direction, text),
            task_callback=lambda task: self.bridge.task_changed.emit(task),
        )

        self._build_actions()
        self._build_ui()
        self._load_sites()
        self._apply_theme()

    def _build_actions(self) -> None:
        style = QApplication.style()
        self.site_manager_action = QAction(style.standardIcon(QStyle.SP_DriveNetIcon), "站点管理", self)
        self.connect_action = QAction(style.standardIcon(QStyle.SP_DialogApplyButton), "连接", self)
        self.disconnect_action = QAction(style.standardIcon(QStyle.SP_DialogCloseButton), "断开", self)
        self.refresh_action = QAction(style.standardIcon(QStyle.SP_BrowserReload), "刷新", self)
        self.up_action = QAction(style.standardIcon(QStyle.SP_ArrowUp), "上级", self)
        self.upload_action = QAction(style.standardIcon(QStyle.SP_ArrowUp), "上传", self)
        self.download_action = QAction(style.standardIcon(QStyle.SP_ArrowDown), "下载", self)
        self.mkdir_action = QAction(style.standardIcon(QStyle.SP_FileDialogNewFolder), "新建目录", self)
        self.rename_action = QAction(style.standardIcon(QStyle.SP_FileDialogDetailedView), "重命名", self)
        self.delete_action = QAction(style.standardIcon(QStyle.SP_TrashIcon), "删除", self)
        self.properties_action = QAction(style.standardIcon(QStyle.SP_FileDialogInfoView), "属性", self)
        self.theme_action = QAction("切换主题", self)
        self.local_up_action = QAction(style.standardIcon(QStyle.SP_ArrowUp), "本地上级", self)
        self.local_choose_action = QAction(style.standardIcon(QStyle.SP_DirOpenIcon), "选择本地目录", self)
        self.quit_action = QAction("退出", self)

        self.site_manager_action.triggered.connect(self.show_site_manager)
        self.connect_action.triggered.connect(self.connect_to_server)
        self.disconnect_action.triggered.connect(self.disconnect_from_server)
        self.refresh_action.triggered.connect(self.refresh_remote)
        self.up_action.triggered.connect(self.remote_up)
        self.upload_action.triggered.connect(self.upload_file)
        self.download_action.triggered.connect(self.download_selected)
        self.mkdir_action.triggered.connect(self.create_remote_folder)
        self.rename_action.triggered.connect(self.rename_remote_item)
        self.delete_action.triggered.connect(self.delete_remote_item)
        self.properties_action.triggered.connect(self.show_remote_properties)
        self.theme_action.triggered.connect(self.toggle_theme)
        self.local_up_action.triggered.connect(self.local_up)
        self.local_choose_action.triggered.connect(self.choose_local_root)
        self.quit_action.triggered.connect(self.close)

    def _build_ui(self) -> None:
        self._build_menu_bar()
        self._build_site_manager_dialog()

        central = QWidget()
        central.setObjectName("AppRoot")
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 8, 10, 8)
        root_layout.setSpacing(6)

        root_layout.addWidget(self._build_commander_toolbar())
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("WorkspaceSplitter")
        splitter.addWidget(self._build_local_panel())
        splitter.addWidget(self._build_remote_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, 1)

        tabs = QTabWidget()
        tabs.setObjectName("BottomTabs")
        tabs.addTab(self._build_transfer_table(), "传输队列")
        tabs.addTab(self._build_log_panel(), "协议日志")
        tabs.setMaximumHeight(230)
        root_layout.addWidget(tabs)

        self.setCentralWidget(central)
        self.statusBar().showMessage("未连接。打开“站点管理”连接 FTP 服务器。")

    def _build_menu_bar(self) -> None:
        session_menu = self.menuBar().addMenu("会话")
        session_menu.addAction(self.site_manager_action)
        session_menu.addAction(self.connect_action)
        session_menu.addAction(self.disconnect_action)
        session_menu.addSeparator()
        session_menu.addAction(self.quit_action)

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self.upload_action)
        file_menu.addAction(self.download_action)
        file_menu.addAction(self.refresh_action)
        file_menu.addAction(self.up_action)
        file_menu.addSeparator()
        file_menu.addAction(self.mkdir_action)
        file_menu.addAction(self.rename_action)
        file_menu.addAction(self.delete_action)
        file_menu.addAction(self.properties_action)

        view_menu = self.menuBar().addMenu("查看")
        view_menu.addAction(self.theme_action)

    def _build_site_manager_dialog(self) -> None:
        self.site_dialog = QDialog(self)
        self.site_dialog.setWindowTitle("站点管理")
        self.site_dialog.resize(680, 430)
        layout = QHBoxLayout(self.site_dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        left = QVBoxLayout()
        title = QLabel("已保存站点")
        title.setObjectName("DialogTitle")
        self.site_list = QListWidget()
        self.site_list.setAlternatingRowColors(True)
        self.site_list.itemDoubleClicked.connect(self.load_selected_site)
        delete_button = QPushButton("删除站点")
        delete_button.setProperty("kind", "danger")
        delete_button.clicked.connect(self.delete_selected_site)
        left.addWidget(title)
        left.addWidget(self.site_list, 1)
        left.addWidget(delete_button)

        form_box = QGroupBox("连接配置")
        form = QGridLayout(form_box)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("ftp.example.com")
        self.port_edit = QLineEdit("21")
        self.user_edit = QLineEdit("anonymous")
        self.password_edit = QLineEdit("anonymous@")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.site_name_edit = QLineEdit()
        self.site_name_edit.setPlaceholderText("我的 FTP 站点")
        self.save_password_check = QCheckBox("保存密码")
        connect_button = QPushButton("连接")
        connect_button.setProperty("kind", "primary")
        save_site_button = QPushButton("保存站点")
        close_button = QPushButton("关闭")
        connect_button.clicked.connect(self.connect_to_server)
        save_site_button.clicked.connect(self.save_current_site)
        close_button.clicked.connect(self.site_dialog.close)

        form.addWidget(QLabel("站点名"), 0, 0)
        form.addWidget(self.site_name_edit, 0, 1, 1, 3)
        form.addWidget(QLabel("主机"), 1, 0)
        form.addWidget(self.host_edit, 1, 1, 1, 3)
        form.addWidget(QLabel("端口"), 2, 0)
        form.addWidget(self.port_edit, 2, 1)
        form.addWidget(QLabel("用户"), 3, 0)
        form.addWidget(self.user_edit, 3, 1, 1, 3)
        form.addWidget(QLabel("密码"), 4, 0)
        form.addWidget(self.password_edit, 4, 1, 1, 3)
        form.addWidget(self.save_password_check, 5, 1, 1, 2)
        form.addWidget(connect_button, 6, 1)
        form.addWidget(save_site_button, 6, 2)
        form.addWidget(close_button, 6, 3)
        form.setColumnStretch(1, 1)

        layout.addLayout(left, 2)
        layout.addWidget(form_box, 3)

    def _build_commander_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("CommanderToolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        for action, kind in [
            (self.site_manager_action, "secondary"),
            (self.connect_action, "primary"),
            (self.disconnect_action, "secondary"),
            (self.upload_action, "primary"),
            (self.download_action, "primary"),
            (self.refresh_action, "secondary"),
            (self.up_action, "secondary"),
            (self.mkdir_action, "secondary"),
            (self.rename_action, "secondary"),
            (self.delete_action, "danger"),
            (self.properties_action, "secondary"),
            (self.theme_action, "secondary"),
        ]:
            button = QPushButton(action.text())
            button.setIcon(action.icon())
            button.setToolTip(action.text())
            button.setProperty("kind", kind)
            button.clicked.connect(action.trigger)
            layout.addWidget(button)
        layout.addStretch(1)
        self.connection_badge = QLabel("未连接")
        self.connection_badge.setObjectName("StatusBadge")
        self.connection_badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.connection_badge)
        return bar

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Hero")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 14, 18, 14)

        title_block = QVBoxLayout()
        title = QLabel("Socket FTP Client")
        title.setObjectName("AppTitle")
        subtitle = QLabel("FTP 文件传输工作台")
        subtitle.setObjectName("AppSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)

        self.connection_badge = QLabel("未连接")
        self.connection_badge.setObjectName("StatusBadge")
        self.connection_badge.setAlignment(Qt.AlignCenter)
        self.connection_badge.setMinimumWidth(86)

        layout.addLayout(title_block, 1)
        layout.addWidget(self.connection_badge)
        return header

    def _build_connection_panel(self) -> QWidget:
        connection_box = QGroupBox("连接信息")
        connection_box.setObjectName("ConnectionPanel")
        form = QGridLayout(connection_box)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("ftp.example.com")
        self.port_edit = QLineEdit("21")
        self.user_edit = QLineEdit("anonymous")
        self.password_edit = QLineEdit("anonymous@")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.site_name_edit = QLineEdit()
        self.site_name_edit.setPlaceholderText("我的 FTP 站点")
        self.save_password_check = QCheckBox("保存密码")
        connect_button = QPushButton("连接服务器")
        connect_button.setProperty("kind", "primary")
        save_site_button = QPushButton("保存站点")
        connect_button.clicked.connect(self.connect_to_server)
        save_site_button.clicked.connect(self.save_current_site)

        form.addWidget(QLabel("站点名"), 0, 0)
        form.addWidget(self.site_name_edit, 0, 1)
        form.addWidget(QLabel("主机"), 0, 2)
        form.addWidget(self.host_edit, 0, 3)
        form.addWidget(QLabel("端口"), 0, 4)
        form.addWidget(self.port_edit, 0, 5)
        form.addWidget(QLabel("用户"), 1, 0)
        form.addWidget(self.user_edit, 1, 1)
        form.addWidget(QLabel("密码"), 1, 2)
        form.addWidget(self.password_edit, 1, 3)
        form.addWidget(self.save_password_check, 1, 4)
        form.addWidget(connect_button, 1, 5)
        form.addWidget(save_site_button, 1, 6)
        form.setColumnStretch(1, 2)
        form.setColumnStretch(3, 3)
        return connection_box

    def _build_command_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("CommandBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        for action, kind in [
            (self.upload_action, "primary"),
            (self.download_action, "primary"),
            (self.refresh_action, "secondary"),
            (self.up_action, "secondary"),
            (self.mkdir_action, "secondary"),
            (self.rename_action, "secondary"),
            (self.delete_action, "danger"),
            (self.properties_action, "secondary"),
            (self.disconnect_action, "secondary"),
            (self.theme_action, "secondary"),
        ]:
            button = QPushButton(action.text())
            button.setIcon(action.icon())
            button.setToolTip(action.text())
            button.setProperty("kind", kind)
            button.clicked.connect(action.trigger)
            layout.addWidget(button)
        layout.addStretch(1)
        return bar

    def _build_site_panel(self) -> QWidget:
        panel = QGroupBox("站点")
        panel.setMinimumWidth(220)
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        self.site_list = QListWidget()
        self.site_list.setAlternatingRowColors(True)
        self.site_list.itemDoubleClicked.connect(self.load_selected_site)
        delete_button = QPushButton("删除站点")
        delete_button.setProperty("kind", "danger")
        delete_button.clicked.connect(self.delete_selected_site)
        layout.addWidget(self.site_list)
        layout.addWidget(delete_button)
        return panel

    def _build_local_panel(self) -> QWidget:
        panel = QGroupBox("左侧：本地文件")
        panel.setMinimumWidth(380)
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        path_row = QHBoxLayout()
        self.local_path_label = QLabel(str(self.current_local_path))
        self.local_path_label.setObjectName("PathLabel")
        local_up_button = QPushButton("上级")
        local_choose_button = QPushButton("选择目录")
        local_up_button.clicked.connect(self.local_up)
        local_choose_button.clicked.connect(self.choose_local_root)
        path_row.addWidget(self.local_path_label, 1)
        path_row.addWidget(local_up_button)
        path_row.addWidget(local_choose_button)

        self.local_table = FileTableWidget("local")
        self.local_table.setColumnCount(4)
        self.local_table.setHorizontalHeaderLabels(["名称", "大小", "类型", "修改时间"])
        self.local_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.local_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.local_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.local_table.setAlternatingRowColors(True)
        self.local_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_table.verticalHeader().setVisible(False)
        self.local_table.verticalHeader().setDefaultSectionSize(34)
        self.local_table.doubleClicked.connect(self.local_double_clicked)
        self.local_table.customContextMenuRequested.connect(self.show_local_context_menu)
        self.local_table.files_dropped.connect(self.handle_drop_on_local)
        self.local_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 4):
            self.local_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.local_status_label = QLabel("本地目录")
        self.local_status_label.setObjectName("PanelStatus")
        layout.addLayout(path_row)
        layout.addWidget(self.local_table)
        layout.addWidget(self.local_status_label)
        self.render_local_directory(self.current_local_path)
        return panel

    def _build_remote_panel(self) -> QWidget:
        panel = QGroupBox("右侧：远端服务器")
        panel.setMinimumWidth(480)
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        path_row = QHBoxLayout()
        self.remote_path_edit = QLineEdit("/")
        go_button = QPushButton("打开")
        go_button.clicked.connect(self.go_remote_path)
        path_row.addWidget(QLabel("远端路径"))
        path_row.addWidget(self.remote_path_edit, 1)
        path_row.addWidget(go_button)
        layout.addLayout(path_row)

        self.remote_table = FileTableWidget("remote")
        self.remote_table.setColumnCount(5)
        self.remote_table.setHorizontalHeaderLabels(["名称", "类型", "大小", "修改时间", "权限"])
        self.remote_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.remote_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.remote_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.remote_table.setAlternatingRowColors(True)
        self.remote_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_table.verticalHeader().setVisible(False)
        self.remote_table.verticalHeader().setDefaultSectionSize(34)
        self.remote_table.doubleClicked.connect(self.remote_double_clicked)
        self.remote_table.customContextMenuRequested.connect(self.show_remote_context_menu)
        self.remote_table.files_dropped.connect(self.handle_drop_on_remote)
        self.remote_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.remote_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.remote_status_label = QLabel("未连接")
        self.remote_status_label.setObjectName("PanelStatus")
        layout.addWidget(self.remote_table)
        layout.addWidget(self.remote_status_label)
        return panel

    def _build_transfer_table(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        self.transfer_table = QTableWidget(0, 7)
        self.transfer_table.setHorizontalHeaderLabels(
            ["ID", "方向", "路径", "状态", "进度", "速度", "错误"]
        )
        self.transfer_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.transfer_table.setAlternatingRowColors(True)
        self.transfer_table.verticalHeader().setVisible(False)
        self.transfer_table.verticalHeader().setDefaultSectionSize(34)
        self.transfer_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.transfer_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        controls = QHBoxLayout()
        pause_button = QPushButton("暂停")
        resume_button = QPushButton("继续 / 重试")
        cancel_button = QPushButton("取消")
        cancel_button.setProperty("kind", "danger")
        pause_button.clicked.connect(self.pause_selected_task)
        resume_button.clicked.connect(self.resume_selected_task)
        cancel_button.clicked.connect(self.cancel_selected_task)
        controls.addWidget(pause_button)
        controls.addWidget(resume_button)
        controls.addWidget(cancel_button)
        controls.addStretch(1)
        layout.addWidget(self.transfer_table)
        layout.addLayout(controls)
        return widget

    def _build_log_panel(self) -> QWidget:
        self.log_text = QTextEdit()
        self.log_text.setObjectName("ProtocolLog")
        self.log_text.setReadOnly(True)
        return self.log_text

    @Slot()
    def show_site_manager(self) -> None:
        self._load_sites()
        self.site_dialog.show()
        self.site_dialog.raise_()
        self.site_dialog.activateWindow()

    @Slot()
    def connect_to_server(self) -> None:
        if not self.host_edit.text().strip():
            self.show_site_manager()
            return
        config = self._config_from_fields()
        self.disconnect_from_server(silent=True)
        self._set_remote_busy(True, f"正在连接 {config.host}:{config.port} ...")

        def worker() -> None:
            session = FtpSession(log_callback=lambda direction, text: self.bridge.log_received.emit(direction, text))
            error: Exception | None = None
            try:
                session.connect(
                    config.host,
                    config.port,
                    config.username,
                    config.password,
                    config.timeout,
                    config.passive_mode,
                )
            except Exception as exc:
                error = exc
                session.close()
            self.bridge.connection_finished.emit(config, session if error is None else None, error)

        threading.Thread(target=worker, name="ftp-connect", daemon=True).start()

    @Slot(object, object, object)
    def on_connection_finished(
        self,
        config: FtpConnectionConfig,
        session: FtpSession | None,
        error: Exception | None,
    ) -> None:
        self._set_remote_busy(False)
        if error or not session:
            self._show_error("连接失败", error or RuntimeError("未知连接错误"))
            return
        self.session = session
        self.active_config = config
        self.remote_path_edit.setText(session.current_directory)
        self._set_connection_state(True, config)
        self.statusBar().showMessage(f"已连接 {config.host}:{config.port}")
        self.site_dialog.close()
        self.save_current_site()
        self.refresh_remote()

    def _list_remote_in_background(self, action) -> None:
        if not self.session or self.remote_busy:
            return
        self._set_remote_busy(True, "正在读取远端目录 ...")

        def worker() -> None:
            entries: list[FtpEntry] | None = None
            path = ""
            error: Exception | None = None
            try:
                action()
                assert self.session is not None
                entries = self.session.list()
                path = self.session.current_directory
            except Exception as exc:
                error = exc
            self.bridge.remote_list_finished.emit(entries, path, error)

        threading.Thread(target=worker, name="ftp-list", daemon=True).start()

    @Slot(object, object, object)
    def on_remote_list_finished(
        self,
        entries: list[FtpEntry] | None,
        path: str,
        error: Exception | None,
    ) -> None:
        self._set_remote_busy(False)
        if error:
            self._show_error("读取远端目录失败", error)
            return
        self.remote_entries = sorted(entries or [], key=lambda item: (not item.is_dir, item.name.lower()))
        self.remote_path_edit.setText(path or "/")
        self._render_remote_entries()
        self.remote_status_label.setText(f"{path or '/'}  |  {len(self.remote_entries)} 个项目")
        self.statusBar().showMessage(f"远端目录包含 {len(self.remote_entries)} 个项目")


    @Slot()
    def disconnect_from_server(self, silent: bool = False) -> None:
        if self.session:
            self.session.close()
        self.session = None
        self.active_config = None
        self.remote_entries = []
        self.remote_table.setRowCount(0)
        self.remote_status_label.setText("未连接")
        self._set_connection_state(False)
        if not silent:
            self.statusBar().showMessage("已断开连接")

    @Slot()
    def refresh_remote(self) -> None:
        self._list_remote_in_background(lambda: None)

    @Slot()
    def remote_up(self) -> None:
        if not self.session:
            return
        self._list_remote_in_background(lambda: self.session_or_raise().cdup())

    @Slot()
    def go_remote_path(self) -> None:
        if not self.session:
            return
        target = self.remote_path_edit.text().strip() or "/"
        self._list_remote_in_background(lambda: self.session_or_raise().cwd(target))

    @Slot(QModelIndex)
    def local_double_clicked(self, index: QModelIndex) -> None:
        if index.row() < 0 or index.row() >= len(self.local_entries):
            return
        entry = self.local_entries[index.row()]
        if entry.is_parent:
            self.local_up()
        elif entry.is_dir:
            self.render_local_directory(entry.path)

    @Slot()
    def local_up(self) -> None:
        parent = self.current_local_path.parent
        if parent == self.current_local_path:
            return
        self.render_local_directory(parent)

    @Slot()
    def choose_local_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择本地目录", str(self.current_local_path))
        if not directory:
            return
        self.render_local_directory(Path(directory))

    @Slot(QModelIndex)
    def remote_double_clicked(self, index: QModelIndex) -> None:
        row = index.row()
        if row == 0:
            self.remote_up()
            return
        entry_index = row - 1
        if entry_index < 0 or entry_index >= len(self.remote_entries):
            return
        entry = self.remote_entries[entry_index]
        if entry.type in {FtpEntryType.DIRECTORY, FtpEntryType.LINK}:
            self._list_remote_in_background(lambda: self.session_or_raise().cwd(entry.name))

    @Slot(QPoint)
    def show_local_context_menu(self, position: QPoint) -> None:
        self._select_context_row(self.local_table, position)
        entry = self.selected_local_entry()
        menu = QMenu(self)
        open_action = menu.addAction("打开")
        upload_action = menu.addAction("上传到远端")
        menu.addSeparator()
        new_folder_action = menu.addAction("新建目录")
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        properties_action = menu.addAction("属性")
        refresh_action = menu.addAction("刷新")

        if not entry:
            open_action.setEnabled(False)
            upload_action.setEnabled(False)
            rename_action.setEnabled(False)
            delete_action.setEnabled(False)
            properties_action.setEnabled(False)
        elif entry.is_parent:
            upload_action.setEnabled(False)
            rename_action.setEnabled(False)
            delete_action.setEnabled(False)

        selected = menu.exec(self.local_table.viewport().mapToGlobal(position))
        if selected == open_action and entry:
            self.open_local_entry(entry)
        elif selected == upload_action:
            self.upload_file()
        elif selected == new_folder_action:
            self.create_local_folder()
        elif selected == rename_action:
            self.rename_local_item()
        elif selected == delete_action:
            self.delete_local_item()
        elif selected == properties_action:
            self.show_local_properties()
        elif selected == refresh_action:
            self.render_local_directory(self.current_local_path)

    @Slot(QPoint)
    def show_remote_context_menu(self, position: QPoint) -> None:
        self._select_context_row(self.remote_table, position)
        row = self.remote_table.currentRow()
        entry = self.selected_remote_entry()
        menu = QMenu(self)
        open_action = menu.addAction("打开")
        download_action = menu.addAction("下载到左侧")
        menu.addSeparator()
        new_folder_action = menu.addAction("新建目录")
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        properties_action = menu.addAction("属性")
        refresh_action = menu.addAction("刷新")

        if row == 0:
            download_action.setEnabled(False)
            rename_action.setEnabled(False)
            delete_action.setEnabled(False)
            properties_action.setEnabled(False)
        elif not entry:
            open_action.setEnabled(False)
            download_action.setEnabled(False)
            rename_action.setEnabled(False)
            delete_action.setEnabled(False)
            properties_action.setEnabled(False)
        elif entry.is_dir:
            download_action.setEnabled(False)

        selected = menu.exec(self.remote_table.viewport().mapToGlobal(position))
        if selected == open_action:
            if row == 0:
                self.remote_up()
            elif entry and entry.is_dir:
                self._list_remote_in_background(lambda: self.session_or_raise().cwd(entry.name))
        elif selected == download_action:
            self.download_selected()
        elif selected == new_folder_action:
            self.create_remote_folder()
        elif selected == rename_action:
            self.rename_remote_item()
        elif selected == delete_action:
            self.delete_remote_item()
        elif selected == properties_action:
            self.show_remote_properties()
        elif selected == refresh_action:
            self.refresh_remote()

    @Slot(str)
    def handle_drop_on_remote(self, source_panel: str) -> None:
        if source_panel == "local":
            self.upload_file()

    @Slot(str)
    def handle_drop_on_local(self, source_panel: str) -> None:
        if source_panel == "remote":
            self.download_selected()

    @Slot()
    def upload_file(self) -> None:
        if not self.active_config:
            self._show_message("未连接", "请先连接 FTP 服务器。")
            return
        entry = self.selected_local_entry()
        path = entry.path if entry else None
        if entry and entry.is_parent:
            self._show_message("上传", "请选择具体文件上传，不能上传 '..'。")
            return
        if path is None or path.is_dir():
            if path is not None and path.is_dir():
                self._show_message("上传", "当前版本暂不支持递归上传目录，请选择单个文件。")
                return
            chosen, _ = QFileDialog.getOpenFileName(self, "选择要上传的文件", self.local_path_label.text())
            if not chosen:
                return
            path = Path(chosen)
        remote_path = remote_join(self.remote_path_edit.text(), Path(path).name)
        self.transfer_manager.enqueue_upload(path, remote_path, resume=True)

    @Slot()
    def download_selected(self) -> None:
        entry = self.selected_remote_entry()
        if not entry:
            return
        if entry.is_dir:
            self._show_message("下载", "当前版本请选择单个文件下载。")
            return
        local_path = self.current_local_path / entry.name
        remote_path = remote_join(self.remote_path_edit.text(), entry.name)
        self.transfer_manager.enqueue_download(remote_path, local_path, resume=True)

    @Slot()
    def create_remote_folder(self) -> None:
        if not self.session:
            return
        name, ok = QInputDialog.getText(self, "新建目录", "目录名称：")
        if ok and name.strip():
            try:
                self.session.mkdir(name.strip())
                self.refresh_remote()
            except Exception as exc:
                self._show_error("新建目录失败", exc)

    @Slot()
    def rename_remote_item(self) -> None:
        entry = self.selected_remote_entry()
        if not entry:
            return
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=entry.name)
        if ok and new_name.strip() and new_name.strip() != entry.name:
            try:
                self.session_or_raise().rename(entry.name, new_name.strip())
                self.refresh_remote()
            except Exception as exc:
                self._show_error("重命名失败", exc)

    @Slot()
    def delete_remote_item(self) -> None:
        entry = self.selected_remote_entry()
        if not entry:
            return
        answer = QMessageBox.question(self, "删除", f"确定删除 '{entry.name}' 吗？")
        if answer != QMessageBox.Yes:
            return
        try:
            session = self.session_or_raise()
            if entry.is_dir:
                session.rmdir(entry.name)
            else:
                session.delete(entry.name)
            self.refresh_remote()
        except Exception as exc:
            self._show_error("删除失败", exc)

    @Slot()
    def show_remote_properties(self) -> None:
        entry = self.selected_remote_entry()
        if not entry:
            return
        details = [
            f"名称：{entry.name}",
            f"类型：{format_entry_type(entry)}",
            f"大小：{format_size(entry.size)}",
            f"修改时间：{entry.modified or '-'}",
            f"权限：{entry.permissions or '-'}",
            f"原始列表行：{entry.raw}",
        ]
        QMessageBox.information(self, "属性", "\n".join(details))

    def open_local_entry(self, entry: LocalEntry) -> None:
        if entry.is_parent:
            self.local_up()
        elif entry.is_dir:
            self.render_local_directory(entry.path)
        else:
            self.show_local_properties()

    def create_local_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "新建本地目录", "目录名称：")
        if not ok or not name.strip():
            return
        target = self.current_local_path / name.strip()
        try:
            target.mkdir()
            self.render_local_directory(self.current_local_path)
        except OSError as exc:
            self._show_error("新建本地目录失败", exc)

    def rename_local_item(self) -> None:
        entry = self.selected_local_entry()
        if not entry or entry.is_parent:
            return
        new_name, ok = QInputDialog.getText(self, "重命名本地项目", "新名称：", text=entry.name)
        if not ok or not new_name.strip() or new_name.strip() == entry.name:
            return
        try:
            entry.path.rename(entry.path.with_name(new_name.strip()))
            self.render_local_directory(self.current_local_path)
        except OSError as exc:
            self._show_error("重命名本地项目失败", exc)

    def delete_local_item(self) -> None:
        entry = self.selected_local_entry()
        if not entry or entry.is_parent:
            return
        answer = QMessageBox.question(self, "删除", f"确定删除本地项目 '{entry.name}' 吗？")
        if answer != QMessageBox.Yes:
            return
        try:
            if entry.is_dir:
                entry.path.rmdir()
            else:
                entry.path.unlink()
            self.render_local_directory(self.current_local_path)
        except OSError as exc:
            self._show_error("删除本地项目失败", exc)

    def show_local_properties(self) -> None:
        entry = self.selected_local_entry()
        if not entry:
            return
        details = [
            f"名称：{entry.name}",
            f"路径：{entry.path}",
            f"类型：{'上级目录' if entry.is_parent else ('目录' if entry.is_dir else '文件')}",
            f"大小：{format_size(entry.size)}",
            f"修改时间：{entry.modified or '-'}",
        ]
        QMessageBox.information(self, "本地属性", "\n".join(details))

    @Slot()
    def save_current_site(self) -> None:
        try:
            site = Site(
                id=None,
                name=self.site_name_edit.text().strip() or self.host_edit.text().strip(),
                host=self.host_edit.text().strip(),
                port=int(self.port_edit.text().strip() or "21"),
                username=self.user_edit.text().strip() or "anonymous",
                save_password=self.save_password_check.isChecked(),
                password=self.password_edit.text(),
            )
            if site.host:
                self.store.save_site(site)
                self._load_sites()
        except Exception as exc:
            self._show_error("保存站点失败", exc)

    @Slot()
    def load_selected_site(self) -> None:
        item = self.site_list.currentItem()
        if not item:
            return
        site: Site = item.data(Qt.UserRole)
        self.site_name_edit.setText(site.name)
        self.host_edit.setText(site.host)
        self.port_edit.setText(str(site.port))
        self.user_edit.setText(site.username)
        self.password_edit.setText(site.password)
        self.save_password_check.setChecked(site.save_password)

    @Slot()
    def delete_selected_site(self) -> None:
        item = self.site_list.currentItem()
        if not item:
            return
        site: Site = item.data(Qt.UserRole)
        if site.id is not None:
            self.store.delete_site(site.id)
            self._load_sites()

    @Slot()
    def pause_selected_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id:
            self.transfer_manager.pause_task(task_id)

    @Slot()
    def resume_selected_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id:
            self.transfer_manager.resume_task(task_id)

    @Slot()
    def cancel_selected_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id:
            self.transfer_manager.cancel_task(task_id)

    @Slot(str, str)
    def append_log(self, direction: str, text: str) -> None:
        color = "#0b7a35" if direction == "C" else "#1f4e8c"
        self.log_text.append(f'<span style="color:{color};font-weight:600">{direction}</span> {escape_html(text)}')

    @Slot(object)
    def update_task_row(self, task: TransferTask) -> None:
        row = self.task_rows.get(task.id)
        if row is None:
            row = self.transfer_table.rowCount()
            self.transfer_table.insertRow(row)
            self.task_rows[task.id] = row
            progress = QProgressBar()
            progress.setRange(0, 100)
            self.transfer_table.setCellWidget(row, 4, progress)

        values = [
            str(task.id),
            "下载" if task.request.kind == TransferKind.DOWNLOAD else "上传",
            str(task.request.local_path if task.request.kind == TransferKind.UPLOAD else task.request.remote_path),
            format_transfer_status(task.status),
            "",
            f"{format_size(int(task.speed))}/s" if task.speed else "-",
            task.error,
        ]
        for column, value in enumerate(values):
            if column == 4:
                continue
            item = self.transfer_table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self.transfer_table.setItem(row, column, item)
            item.setText(value)
        progress = self.transfer_table.cellWidget(row, 4)
        if isinstance(progress, QProgressBar):
            progress.setValue(task.percent if task.status != TransferStatus.COMPLETED else 100)
            progress.setFormat(f"{task.percent}% ({format_size(task.transferred)}/{format_size(task.total)})")
        if (
            task.status == TransferStatus.COMPLETED
            and task.request.kind == TransferKind.DOWNLOAD
            and task.request.local_path.parent == self.current_local_path
        ):
            self.render_local_directory(self.current_local_path)

    @Slot()
    def toggle_theme(self) -> None:
        self.dark_theme = not self.dark_theme
        self._apply_theme()

    def selected_remote_entry(self) -> FtpEntry | None:
        indexes = self.remote_table.selectionModel().selectedRows()
        if not indexes:
            return None
        row = indexes[0].row()
        if row == 0:
            return None
        entry_index = row - 1
        if 0 <= entry_index < len(self.remote_entries):
            return self.remote_entries[entry_index]
        return None

    def selected_local_path(self) -> Path | None:
        entry = self.selected_local_entry()
        return entry.path if entry else None

    def selected_local_entry(self) -> LocalEntry | None:
        indexes = self.local_table.selectionModel().selectedRows()
        if not indexes:
            return None
        row = indexes[0].row()
        if 0 <= row < len(self.local_entries):
            return self.local_entries[row]
        return None

    def selected_task_id(self) -> int | None:
        indexes = self.transfer_table.selectionModel().selectedRows()
        if not indexes:
            return None
        item = self.transfer_table.item(indexes[0].row(), 0)
        return int(item.text()) if item else None

    def session_or_raise(self) -> FtpSession:
        if not self.session:
            raise FtpError("Not connected")
        return self.session

    def _render_remote_entries(self) -> None:
        icon_provider = QFileIconProvider()
        icon_type = QFileIconProvider.IconType if hasattr(QFileIconProvider, "IconType") else QFileIconProvider
        self.remote_table.setRowCount(len(self.remote_entries) + 1)
        parent_item = QTableWidgetItem("..")
        parent_item.setIcon(icon_provider.icon(icon_type.Folder))
        parent_cells = [
            parent_item,
            QTableWidgetItem("上级目录"),
            QTableWidgetItem(""),
            QTableWidgetItem(""),
            QTableWidgetItem(""),
        ]
        for column, item in enumerate(parent_cells):
            self.remote_table.setItem(0, column, item)

        for row, entry in enumerate(self.remote_entries, start=1):
            name_item = QTableWidgetItem(entry.name)
            if entry.is_dir:
                name_item.setIcon(icon_provider.icon(icon_type.Folder))
            else:
                name_item.setIcon(icon_provider.icon(icon_type.File))
            cells = [
                name_item,
                QTableWidgetItem(format_entry_type(entry)),
                QTableWidgetItem(format_size(entry.size)),
                QTableWidgetItem(entry.modified.strftime("%Y-%m-%d %H:%M") if entry.modified else "-"),
                QTableWidgetItem(entry.permissions or "-"),
            ]
            for column, item in enumerate(cells):
                self.remote_table.setItem(row, column, item)

    def render_local_directory(self, directory: Path) -> None:
        directory = directory.expanduser().resolve()
        if not directory.exists() or not directory.is_dir():
            self._show_message("本地目录", f"无法打开本地目录：{directory}")
            return

        entries: list[LocalEntry] = []
        parent = directory.parent
        if parent != directory:
            entries.append(LocalEntry("..", parent, True, None, None, is_parent=True))
        try:
            children = []
            for child in directory.iterdir():
                try:
                    stat = child.stat()
                    children.append(
                        LocalEntry(
                            name=child.name,
                            path=child,
                            is_dir=child.is_dir(),
                            size=None if child.is_dir() else stat.st_size,
                            modified=datetime.fromtimestamp(stat.st_mtime),
                        )
                    )
                except OSError:
                    continue
            children.sort(key=lambda item: (not item.is_dir, item.name.lower()))
            entries.extend(children)
        except OSError as exc:
            self._show_error("本地目录读取失败", exc)
            return

        self.current_local_path = directory
        self.local_entries = entries
        self.local_path_label.setText(str(directory))
        visible_count = max(0, len(entries) - (1 if entries and entries[0].is_parent else 0))
        self.local_status_label.setText(f"{directory}  |  {visible_count} 个项目")
        self._render_local_entries()

    def _render_local_entries(self) -> None:
        icon_provider = QFileIconProvider()
        icon_type = QFileIconProvider.IconType if hasattr(QFileIconProvider, "IconType") else QFileIconProvider
        self.local_table.setRowCount(len(self.local_entries))
        for row, entry in enumerate(self.local_entries):
            name_item = QTableWidgetItem(entry.name)
            name_item.setIcon(icon_provider.icon(icon_type.Folder if entry.is_dir else icon_type.File))
            cells = [
                name_item,
                QTableWidgetItem("" if entry.is_parent else format_size(entry.size)),
                QTableWidgetItem("上级目录" if entry.is_parent else ("目录" if entry.is_dir else "文件")),
                QTableWidgetItem(entry.modified.strftime("%Y-%m-%d %H:%M") if entry.modified else ""),
            ]
            for column, item in enumerate(cells):
                self.local_table.setItem(row, column, item)

    def _config_from_fields(self) -> FtpConnectionConfig:
        return FtpConnectionConfig(
            host=self.host_edit.text().strip(),
            port=int(self.port_edit.text().strip() or "21"),
            username=self.user_edit.text().strip() or "anonymous",
            password=self.password_edit.text(),
            timeout=15.0,
            passive_mode=True,
        )

    def _load_sites(self) -> None:
        self.site_list.clear()
        for site in self.store.list_sites():
            item = QListWidgetItem(f"{site.name}  ({site.host}:{site.port})")
            item.setData(Qt.UserRole, site)
            self.site_list.addItem(item)

    def _apply_theme(self) -> None:
        if not self.dark_theme:
            self.setStyleSheet(LIGHT_STYLE)
            return
        self.setStyleSheet(DARK_STYLE)

    def _show_error(self, title: str, exc: Exception) -> None:
        self.statusBar().showMessage(f"{title}: {exc}")
        QMessageBox.critical(self, title, str(exc))

    def _show_message(self, title: str, text: str) -> None:
        self.statusBar().showMessage(text)
        QMessageBox.information(self, title, text)

    def _set_connection_state(
        self, connected: bool, config: FtpConnectionConfig | None = None
    ) -> None:
        if connected and config:
            self.connection_badge.setText(f"已连接\n{config.host}")
            self.connection_badge.setProperty("state", "connected")
        else:
            self.connection_badge.setText("未连接")
            self.connection_badge.setProperty("state", "offline")
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)

    def _set_remote_busy(self, busy: bool, message: str = "") -> None:
        self.remote_busy = busy
        for action in [
            self.connect_action,
            self.refresh_action,
            self.up_action,
            self.mkdir_action,
            self.rename_action,
            self.delete_action,
            self.properties_action,
        ]:
            action.setEnabled(not busy)
        self.remote_table.setDisabled(busy)
        if message:
            self.remote_status_label.setText(message)
            self.statusBar().showMessage(message)
        if busy and not self._cursor_busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._cursor_busy = True
        elif not busy and self._cursor_busy:
            QApplication.restoreOverrideCursor()
            self._cursor_busy = False

    def _select_context_row(self, table: QTableWidget, position: QPoint) -> None:
        item = table.itemAt(position)
        if item is not None:
            table.selectRow(item.row())


def remote_join(directory: str, name: str) -> str:
    directory = directory.strip() or "/"
    if directory.endswith("/"):
        return directory + name
    return directory + "/" + name


def format_size(size: int | None) -> str:
    if size is None:
        return "-"
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_entry_type(entry: FtpEntry) -> str:
    if entry.type == FtpEntryType.DIRECTORY:
        return "目录"
    if entry.type == FtpEntryType.FILE:
        return "文件"
    if entry.type == FtpEntryType.LINK:
        return "链接"
    return "未知"


def format_transfer_status(status: TransferStatus) -> str:
    labels = {
        TransferStatus.QUEUED: "排队中",
        TransferStatus.RUNNING: "传输中",
        TransferStatus.PAUSED: "已暂停",
        TransferStatus.COMPLETED: "已完成",
        TransferStatus.FAILED: "失败",
        TransferStatus.CANCELLED: "已取消",
    }
    return labels.get(status, status.value)


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


LIGHT_STYLE = """
* {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "SimHei", "Arial";
    font-size: 13px;
}
QMainWindow, QWidget#AppRoot {
    background: #edf2f7;
    color: #172033;
}
QFrame#Hero {
    background: #152238;
    border: 1px solid #23375c;
    border-radius: 8px;
}
QLabel#AppTitle {
    color: #ffffff;
    font-size: 22px;
    font-weight: 800;
}
QLabel#AppSubtitle {
    color: #b9c7db;
    font-size: 13px;
}
QLabel#StatusBadge {
    background: #39465d;
    color: #d8e4f2;
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 700;
}
QLabel#StatusBadge[state="connected"] {
    background: #d8f7e5;
    color: #12643b;
}
QFrame#CommandBar, QGroupBox {
    background: #ffffff;
    border: 1px solid #d6dee9;
    border-radius: 8px;
}
QGroupBox {
    margin-top: 12px;
    padding: 10px;
    font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #344156;
    background: #edf2f7;
}
QLabel#PathLabel {
    color: #536173;
    background: #f6f8fb;
    border: 1px solid #e1e7ef;
    border-radius: 6px;
    padding: 6px 8px;
}
QLineEdit, QTextEdit, QTableWidget, QListWidget, QTreeView {
    background: #ffffff;
    color: #172033;
    border: 1px solid #ccd6e3;
    border-radius: 7px;
    padding: 5px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
QTableWidget, QTreeView, QListWidget {
    alternate-background-color: #f7f9fc;
    gridline-color: #e8eef6;
}
QPushButton {
    background: #eef3f9;
    color: #263247;
    border: 1px solid #cad5e4;
    border-radius: 7px;
    padding: 7px 13px;
    font-weight: 700;
}
QPushButton:hover { background: #e2eaf5; }
QPushButton[kind="primary"] {
    background: #246bfe;
    color: #ffffff;
    border: 1px solid #246bfe;
}
QPushButton[kind="primary"]:hover { background: #1658de; }
QPushButton[kind="danger"] {
    background: #fff1f1;
    color: #b42318;
    border: 1px solid #ffc7c2;
}
QPushButton[kind="danger"]:hover { background: #ffe1de; }
QHeaderView::section {
    background: #eef3f9;
    color: #263247;
    padding: 8px;
    border: 0;
    border-right: 1px solid #d8e1ec;
    font-weight: 800;
}
QTabWidget::pane {
    border: 1px solid #d6dee9;
    border-radius: 8px;
    background: #ffffff;
}
QTabBar::tab {
    background: #e7edf5;
    color: #344156;
    border: 1px solid #d6dee9;
    padding: 7px 14px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #1d4ed8;
    font-weight: 800;
}
QProgressBar {
    border: 1px solid #ccd6e3;
    border-radius: 6px;
    text-align: center;
    background: #eef3f9;
}
QProgressBar::chunk {
    border-radius: 5px;
    background: #20b486;
}
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #d6dee9;
    color: #536173;
}
"""


DARK_STYLE = """
* {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "SimHei", "Arial";
    font-size: 13px;
}
QMainWindow, QWidget#AppRoot {
    background: #10151f;
    color: #e8edf5;
}
QFrame#Hero {
    background: #0b1020;
    border: 1px solid #223047;
    border-radius: 8px;
}
QLabel#AppTitle {
    color: #ffffff;
    font-size: 22px;
    font-weight: 800;
}
QLabel#AppSubtitle {
    color: #9fb2cc;
    font-size: 13px;
}
QLabel#StatusBadge {
    background: #263244;
    color: #c7d4e6;
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 700;
}
QLabel#StatusBadge[state="connected"] {
    background: #123d2a;
    color: #a8f0c8;
}
QFrame#CommandBar, QGroupBox {
    background: #171f2d;
    border: 1px solid #2a3a53;
    border-radius: 8px;
}
QGroupBox {
    margin-top: 12px;
    padding: 10px;
    font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #d7e0ed;
    background: #10151f;
}
QLabel#PathLabel {
    color: #b8c6da;
    background: #111827;
    border: 1px solid #2a3a53;
    border-radius: 6px;
    padding: 6px 8px;
}
QLineEdit, QTextEdit, QTableWidget, QListWidget, QTreeView {
    background: #0e1420;
    color: #e8edf5;
    border: 1px solid #2a3a53;
    border-radius: 7px;
    padding: 5px;
    selection-background-color: #38bdf8;
    selection-color: #08111f;
}
QTableWidget, QTreeView, QListWidget {
    alternate-background-color: #121a28;
    gridline-color: #263244;
}
QPushButton {
    background: #223047;
    color: #e8edf5;
    border: 1px solid #344862;
    border-radius: 7px;
    padding: 7px 13px;
    font-weight: 700;
}
QPushButton:hover { background: #2d3f59; }
QPushButton[kind="primary"] {
    background: #1d9bf0;
    color: #061221;
    border: 1px solid #1d9bf0;
}
QPushButton[kind="primary"]:hover { background: #4bb6ff; }
QPushButton[kind="danger"] {
    background: #3a1b20;
    color: #ffb4aa;
    border: 1px solid #7a2d34;
}
QPushButton[kind="danger"]:hover { background: #55242a; }
QHeaderView::section {
    background: #1f2b3d;
    color: #e8edf5;
    padding: 8px;
    border: 0;
    border-right: 1px solid #2a3a53;
    font-weight: 800;
}
QTabWidget::pane {
    border: 1px solid #2a3a53;
    border-radius: 8px;
    background: #171f2d;
}
QTabBar::tab {
    background: #141c2a;
    color: #b8c6da;
    border: 1px solid #2a3a53;
    padding: 7px 14px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}
QTabBar::tab:selected {
    background: #171f2d;
    color: #66d9ff;
    font-weight: 800;
}
QProgressBar {
    border: 1px solid #2a3a53;
    border-radius: 6px;
    text-align: center;
    background: #10151f;
}
QProgressBar::chunk {
    border-radius: 5px;
    background: #20b486;
}
QStatusBar {
    background: #171f2d;
    border-top: 1px solid #2a3a53;
    color: #b8c6da;
}
"""
