"""Главное окно DicomBridge: вкладки, трей, мониторинг, очередь, лог."""

import json
import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QStyle, QSystemTrayIcon,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget, QHeaderView, QMenu,
)

from config import APP_VERSION, ConfigManager, resource_path
from core import cleanup as cleanup_mod
from core import encoding_fix
from core import tags as tags_mod
from core.support_bundle import create_support_bundle
from core.logging_setup import subscribe_qt, set_level
from core.processor import Processor
from core.queue import ErrorQueue
from core.sender import PacsSender
from core.watcher import FolderWatcher, is_network_path, validate_folders
from i18n import STR, t

log = logging.getLogger("gui")


class _LogBridge(QObject):
    line = Signal(str)


def sender_from_config(cfg: ConfigManager) -> PacsSender:
    return PacsSender(
        local_aet=cfg.get("PACS", "local_aet", "DICOMBRIDGE"),
        remote_aet=cfg.get("PACS", "remote_aet", "PACS"),
        host=cfg.get("PACS", "host", "127.0.0.1"),
        port=cfg.get_int("PACS", "port", 11112),
        timeout=cfg.get_int("PACS", "timeout_sec", 10),
    )


def set_autostart(enabled: bool, app_name: str = "DicomBridge") -> bool:
    """Автозапуск: Windows — HKCU Run, Linux — XDG autostart. Возвращает успех."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
            )
            with key:
                if enabled:
                    exe = sys.executable if getattr(sys, "frozen", False) else sys.executable
                    if getattr(sys, "frozen", False):
                        cmd = f'"{exe}"'
                    else:
                        script = str(Path(sys.argv[0]).resolve())
                        cmd = f'"{exe}" "{script}" --minimized'
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, app_name)
                    except FileNotFoundError:
                        pass
            return True
        except Exception as exc:
            log.error("автозапуск: %s", exc)
            return False
    if sys.platform.startswith("linux"):
        # XDG autostart: ~/.config/autostart/DicomBridge.desktop
        try:
            adir = Path.home() / ".config" / "autostart"
            adir.mkdir(parents=True, exist_ok=True)
            desktop = adir / f"{app_name}.desktop"
            if enabled:
                if getattr(sys, "frozen", False):
                    cmd = f'"{sys.executable}" --minimized'
                else:
                    script = str(Path(sys.argv[0]).resolve())
                    cmd = f'"{sys.executable}" "{script}" --minimized'
                desktop.write_text(
                    "[Desktop Entry]\nType=Application\n"
                    f"Name={app_name}\nExec={cmd}\n"
                    "X-GNOME-Autostart-enabled=true\n",
                    encoding="utf-8",
                )
            else:
                try:
                    desktop.unlink()
                except FileNotFoundError:
                    pass
            return True
        except Exception as exc:
            log.error("автозапуск (linux): %s", exc)
            return False
    return False


class MainWindow(QMainWindow):
    def __init__(self, cfg: ConfigManager):
        super().__init__()
        self.cfg = cfg
        # имя профиля в заголовке (несколько копий на одном ПК)
        self.setWindowTitle(f"{t('app_title')} [{cfg.profile_name()}]")
        try:
            self.setWindowIcon(QIcon(str(resource_path("assets/logo.png"))))
        except Exception:
            pass
        self.resize(980, 680)

        self.watcher: FolderWatcher | None = None
        self.error_queue = ErrorQueue(cfg.db_path())
        self.processor = Processor(
            cfg, self.error_queue,
            sender_factory=sender_from_config,
            on_event=self._on_proc_event,
            on_stats=self._on_stats,
        )
        self._log_bridge = _LogBridge()
        self._log_bridge.line.connect(self._append_log_line)
        subscribe_qt(lambda msg: self._log_bridge.line.emit(msg))

        self._was_online: bool | None = None
        self._build_ui()
        self._build_tray()
        self._load_settings_to_ui()
        self._load_rules_to_ui()
        self.refresh_queue()
        self.processor.start()
        self._start_timers()
        self._periodic_cleanup()  # как в оригинале: очистка при каждом запуске
        self._update_status()
        self._check_disclaimer()

    def _check_disclaimer(self) -> None:
        """Дисклеймер при первом старте (как лицензионное окно у оригинала)."""
        if self.cfg.get_bool("General", "disclaimer_accepted", False):
            return
        ans = QMessageBox.question(
            self, t("disclaimer_title"), STR["disclaimer_text"],
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans == QMessageBox.Yes:
            self.cfg.set("General", "disclaimer_accepted", True)
            self.cfg.save()
        else:
            # не принял — выходим
            QTimer.singleShot(0, QApplication.instance().quit)

    # ================= построение интерфейса =================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # --- Главная ---
        home = QWidget()
        hl = QVBoxLayout(home)
        stats_box = QGroupBox("Статистика")
        sl = QHBoxLayout(stats_box)
        self.lbl_processed = QLabel("0")
        self.lbl_sent = QLabel("0")
        self.lbl_queued = QLabel("0")
        self.lbl_errors = QLabel("0")
        for title, lbl in (("Обработано", self.lbl_processed),
                           ("Отправлено", self.lbl_sent),
                           ("В очереди", self.lbl_queued),
                           ("Ошибки", self.lbl_errors)):
            box = QVBoxLayout()
            box.addWidget(QLabel(f"<b>{title}</b>"))
            lbl.setStyleSheet("font-size: 22px;")
            box.addWidget(lbl)
            sl.addLayout(box)
        hl.addWidget(stats_box)

        self.lbl_monitor = QLabel("")
        self.lbl_pacs = QLabel("")
        hl.addWidget(self.lbl_monitor)
        hl.addWidget(self.lbl_pacs)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton(t("start"))
        self.btn_stop = QPushButton(t("stop"))
        self.btn_echo = QPushButton(t("test_echo"))
        self.btn_analyze = QPushButton(t("analyze_file"))
        self.btn_start.clicked.connect(self.start_monitoring)
        self.btn_stop.clicked.connect(self.stop_monitoring)
        self.btn_echo.clicked.connect(self.on_test_echo)
        self.btn_analyze.clicked.connect(self.on_analyze)
        for b in (self.btn_start, self.btn_stop, self.btn_echo, self.btn_analyze):
            btn_row.addWidget(b)
        self.btn_about = QPushButton("О программе…")
        self.btn_about.clicked.connect(self.on_about)
        btn_row.addWidget(self.btn_about)
        hl.addLayout(btn_row)
        hl.addStretch(1)
        self.tabs.addTab(home, t("tab_home"))

        # --- Настройки ---
        sett = QWidget()
        form = QFormLayout(sett)
        self.ed_import = QLineEdit()
        self.ed_export = QLineEdit()
        btn_imp = QPushButton("…")
        btn_exp = QPushButton("…")
        btn_imp.clicked.connect(lambda: self._pick_dir(self.ed_import))
        btn_exp.clicked.connect(lambda: self._pick_dir(self.ed_export))
        row1 = QHBoxLayout()
        row1.addWidget(self.ed_import)
        row1.addWidget(btn_imp)
        row2 = QHBoxLayout()
        row2.addWidget(self.ed_export)
        row2.addWidget(btn_exp)
        w1 = QWidget()
        w1.setLayout(row1)
        w2 = QWidget()
        w2.setLayout(row2)
        form.addRow("Папка импорта (только локальная):", w1)
        form.addRow("Папка экспорта:", w2)

        self.ed_logdir = QLineEdit()
        btn_log = QPushButton("…")
        btn_log.clicked.connect(lambda: self._pick_dir(self.ed_logdir))
        row3 = QHBoxLayout()
        row3.addWidget(self.ed_logdir)
        row3.addWidget(btn_log)
        w3 = QWidget()
        w3.setLayout(row3)
        form.addRow("Папка логов (пусто = рядом с программой):", w3)

        clear_row = QHBoxLayout()
        self.btn_clear_export = QPushButton("Очистить папку экспорта")
        self.btn_clear_logs = QPushButton("Очистить папку логов")
        self.btn_clear_export.clicked.connect(self.on_clear_export)
        self.btn_clear_logs.clicked.connect(self.on_clear_logs)
        clear_row.addWidget(self.btn_clear_export)
        clear_row.addWidget(self.btn_clear_logs)
        clearw = QWidget()
        clearw.setLayout(clear_row)
        form.addRow(clearw)

        self.cb_recursive = QCheckBox("включая вложенные папки")
        self.sp_delay = QDoubleSpinBox()
        self.sp_delay.setRange(0, 60)
        self.sp_delay.setSingleStep(0.5)
        self.sp_delay.setSuffix(" сек")
        form.addRow(self.cb_recursive)
        form.addRow("Задержка обработки:", self.sp_delay)

        self.ed_ignore_ext = QLineEdit()
        self.ed_ignore_ext.setPlaceholderText(".tmp .log .txt (через пробел)")
        form.addRow("Игнорировать расширения:", self.ed_ignore_ext)
        self.ed_ignore_name = QLineEdit()
        self.ed_ignore_name.setPlaceholderText("фрагменты имён через ; (регистр не важен)")
        form.addRow("Игнорировать по имени:", self.ed_ignore_name)

        self.cb_check_sig = QCheckBox("проверять сигнатуру DICM")
        form.addRow(self.cb_check_sig)

        self.cmb_encoding = QComboBox()
        self.cmb_encoding.addItem("не менять", "none")
        self.cmb_encoding.addItem("Windows-1251 → ISO_IR 144 (8859-5)", "cp1251_to_ir144")
        self.cmb_encoding.addItem("Windows-1251 → UTF-8 (ISO_IR 192)", "cp1251_to_utf8")
        form.addRow("Кодировка кириллицы:", self.cmb_encoding)

        self.cb_gen_pid = QCheckBox("генерировать Patient ID из ФИО+даты рождения, если пуст")
        self.cb_uid_study = QCheckBox("новый Study UID, если отсутствует/некорректен")
        self.cb_uid_series = QCheckBox("новый Series UID, если отсутствует/некорректен")
        self.cb_uid_sop = QCheckBox("новый SOP UID, если отсутствует/некорректен")
        form.addRow(self.cb_gen_pid)
        form.addRow(self.cb_uid_study)
        form.addRow(self.cb_uid_series)
        form.addRow(self.cb_uid_sop)

        self.cb_translit = QCheckBox("транслитерация кириллицы в латиницу")
        self.cmb_translit = QComboBox()
        self.cmb_translit.addItem("ГОСТ 7.79", "gost")
        self.cmb_translit.addItem("упрощённая", "simple")
        self.cmb_translit.addItem("украинская (КМУ 55)", "ukrainian")
        tr_row = QHBoxLayout()
        tr_row.addWidget(self.cb_translit)
        tr_row.addWidget(self.cmb_translit)
        trw = QWidget()
        trw.setLayout(tr_row)
        form.addRow("Транслитерация:", trw)

        self.cb_anon = QCheckBox("анонимизация (имя → Anonymous)")
        self.cb_keep_id = QCheckBox("сохранить PatientID")
        self.cb_keep_sex = QCheckBox("сохранить пол")
        self.cb_rm_private = QCheckBox("удалять приватные теги")
        form.addRow(self.cb_anon)
        form.addRow(self.cb_keep_id)
        form.addRow(self.cb_keep_sex)
        form.addRow(self.cb_rm_private)
        warn = QLabel(STR["anon_warn"])
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #a33;")
        form.addRow(warn)

        self.ed_local_aet = QLineEdit()
        self.ed_remote_aet = QLineEdit()
        self.ed_host = QLineEdit()
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(1, 120)
        self.cb_send = QCheckBox("отправка в PACS включена (иначе — только конвертер)")
        self.sp_echo = QSpinBox()
        self.sp_echo.setRange(1, 1440)
        self.sp_echo.setSuffix(" мин")
        form.addRow("Локальный AE Title:", self.ed_local_aet)
        form.addRow("AE Title сервера:", self.ed_remote_aet)
        form.addRow("IP/hostname:", self.ed_host)
        form.addRow("Порт:", self.sp_port)
        form.addRow("Таймаут (сек):", self.sp_timeout)
        form.addRow(self.cb_send)
        form.addRow("Проверка связи C-ECHO каждые:", self.sp_echo)

        self.sp_retry = QSpinBox()
        self.sp_retry.setRange(1, 1440)
        self.sp_retry.setSuffix(" мин")
        form.addRow("Автоповтор очереди каждые:", self.sp_retry)

        self.cmb_log = QComboBox()
        self.cmb_log.addItems(["ERROR", "WARN", "INFO", "DEBUG"])
        form.addRow("Уровень лога:", self.cmb_log)
        self.cb_autostart = QCheckBox("автозапуск с Windows")
        self.cb_tray = QCheckBox("сворачивать в трей")
        form.addRow(self.cb_autostart)
        form.addRow(self.cb_tray)

        self.cb_clean_exp = QCheckBox("удалять старые экспортированные файлы старше")
        self.sp_clean_exp = QSpinBox()
        self.sp_clean_exp.setRange(1, 3650)
        self.sp_clean_exp.setSuffix(" дн.")
        self.cb_clean_log = QCheckBox("удалять старые логи старше")
        self.sp_clean_log = QSpinBox()
        self.sp_clean_log.setRange(1, 3650)
        self.sp_clean_log.setSuffix(" дн.")
        form.addRow(self.cb_clean_exp, self.sp_clean_exp)
        form.addRow(self.cb_clean_log, self.sp_clean_log)

        sup_box = QGroupBox("Обслуживание (заполняет инженер при внедрении)")
        sup_form = QFormLayout(sup_box)
        self.ed_org = QLineEdit()
        self.ed_eng = QLineEdit()
        self.ed_phone = QLineEdit()
        sup_form.addRow("Организация:", self.ed_org)
        sup_form.addRow("Инженер:", self.ed_eng)
        sup_form.addRow("Телефон:", self.ed_phone)
        form.addRow(sup_box)

        btn_save = QPushButton("Сохранить настройки")
        btn_save.clicked.connect(self.save_settings_from_ui)
        form.addRow(btn_save)
        self.tabs.addTab(sett, t("tab_settings"))

        # --- Правила ---
        rules = QWidget()
        rl = QVBoxLayout(rules)
        self.tbl_rules = QTableWidget(0, 4)
        self.tbl_rules.setHorizontalHeaderLabels(["Тег", "VR", "Действие", "Значение"])
        self.tbl_rules.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        rl.addWidget(self.tbl_rules)
        rbtn = QHBoxLayout()
        self.btn_rule_add = QPushButton("Добавить")
        self.btn_rule_del = QPushButton("Удалить")
        self.btn_rule_imp = QPushButton("Импорт JSON…")
        self.btn_rule_exp = QPushButton("Экспорт JSON…")
        self.btn_rule_add.clicked.connect(self.on_rule_add)
        self.btn_rule_del.clicked.connect(self.on_rule_del)
        self.btn_rule_imp.clicked.connect(self.on_rule_import)
        self.btn_rule_exp.clicked.connect(self.on_rule_export)
        for b in (self.btn_rule_add, self.btn_rule_del, self.btn_rule_imp, self.btn_rule_exp):
            rbtn.addWidget(b)
        rl.addLayout(rbtn)
        self.tabs.addTab(rules, t("tab_rules"))

        # --- Очередь ---
        qtab = QWidget()
        ql = QVBoxLayout(qtab)
        self.tbl_queue = QTableWidget(0, 4)
        self.tbl_queue.setHorizontalHeaderLabels(["ID", "Файл", "Время", "Причина"])
        self.tbl_queue.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        ql.addWidget(self.tbl_queue)
        qb = QHBoxLayout()
        self.btn_retry_sel = QPushButton("Повторить выбранный")
        self.btn_retry_all = QPushButton(t("retry_all"))
        self.btn_q_del = QPushButton("Удалить выбранный")
        self.btn_q_refresh = QPushButton("Обновить")
        self.btn_retry_sel.clicked.connect(self.on_retry_selected)
        self.btn_retry_all.clicked.connect(self.on_retry_all)
        self.btn_q_del.clicked.connect(self.on_queue_delete)
        self.btn_q_refresh.clicked.connect(self.refresh_queue)
        for b in (self.btn_retry_sel, self.btn_retry_all, self.btn_q_del, self.btn_q_refresh):
            qb.addWidget(b)
        ql.addLayout(qb)
        self.tabs.addTab(qtab, t("tab_queue"))

        # --- Лог ---
        ltab = QWidget()
        ll = QVBoxLayout(ltab)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Фильтр:"))
        self.cmb_log_filter = QComboBox()
        self.cmb_log_filter.addItems(["ALL", "ERROR", "WARN", "INFO", "DEBUG"])
        self.cmb_log_filter.currentTextChanged.connect(self._refilter_log)
        frow.addWidget(self.cmb_log_filter)
        frow.addStretch(1)
        self.btn_bundle = QPushButton("Пакет для поддержки…")
        self.btn_bundle.clicked.connect(self.on_bundle)
        frow.addWidget(self.btn_bundle)
        ll.addLayout(frow)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFontFamily("Consolas")
        ll.addWidget(self.txt_log)
        self._log_lines: list = []
        self._load_recent_log()
        self.tabs.addTab(ltab, t("tab_log"))

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        # логотип программы (без внешнего файла exe не собрать — assets вшит через --add-data)
        try:
            self.tray.setIcon(QIcon(str(resource_path("assets/logo.png"))))
        except Exception:
            self.tray.setIcon(QApplication.style().standardIcon(QStyle.SP_ComputerIcon))
        menu = QMenu()
        act_show = QAction(t("tray_show"), self)
        act_show.triggered.connect(self.showNormal)
        act_quit = QAction(t("tray_quit"), self)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_show)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    # ================= настройки =================
    def _load_settings_to_ui(self) -> None:
        c = self.cfg
        self.ed_import.setText(c.get("Folders", "import_dir", ""))
        self.ed_export.setText(c.get("Folders", "export_dir", ""))
        self.ed_logdir.setText(c.get("Folders", "log_dir", ""))
        self.ed_ignore_ext.setText(c.get("Folders", "ignore_ext", ""))
        self.ed_ignore_name.setText(c.get("Folders", "ignore_name", ""))
        self.cb_recursive.setChecked(c.get_bool("Folders", "recursive", True))
        self.sp_delay.setValue(c.get_float("Folders", "delay_sec", 3.0))
        self.cb_check_sig.setChecked(c.get_bool("Dicom", "check_signature", True))
        self._set_combo(self.cmb_encoding, c.get("Encoding", "mode", "none"))
        self.cb_gen_pid.setChecked(c.get_bool("PatientID", "auto_generate", False))
        self.cb_uid_study.setChecked(c.get_bool("UID", "new_study_if_bad", True))
        self.cb_uid_series.setChecked(c.get_bool("UID", "new_series_if_bad", True))
        self.cb_uid_sop.setChecked(c.get_bool("UID", "new_sop_if_bad", True))
        self.cb_translit.setChecked(c.get_bool("Transliteration", "enabled", False))
        self._set_combo(self.cmb_translit, c.get("Transliteration", "standard", "gost"))
        self.cb_anon.setChecked(c.get_bool("Anonymize", "enabled", False))
        self.cb_keep_id.setChecked(c.get_bool("Anonymize", "keep_patient_id", False))
        self.cb_keep_sex.setChecked(c.get_bool("Anonymize", "keep_sex", False))
        self.cb_rm_private.setChecked(c.get_bool("Anonymize", "remove_private", True))
        self.ed_local_aet.setText(c.get("PACS", "local_aet", "DICOMBRIDGE"))
        self.ed_remote_aet.setText(c.get("PACS", "remote_aet", "PACS"))
        self.ed_host.setText(c.get("PACS", "host", "127.0.0.1"))
        self.sp_port.setValue(c.get_int("PACS", "port", 11112))
        self.sp_timeout.setValue(c.get_int("PACS", "timeout_sec", 10))
        self.cb_send.setChecked(c.get_bool("PACS", "send_enabled", True))
        self.sp_echo.setValue(c.get_int("PACS", "echo_interval_min", 20))
        self.sp_retry.setValue(c.get_int("Queue", "retry_interval_min", 5))
        lvl = c.get("General", "log_level", "INFO")
        idx = self.cmb_log.findText(lvl)
        if idx >= 0:
            self.cmb_log.setCurrentIndex(idx)
        self.cb_autostart.setChecked(c.get_bool("General", "autostart", False))
        self.cb_tray.setChecked(c.get_bool("General", "minimize_to_tray", True))
        self.cb_clean_exp.setChecked(c.get_bool("Cleanup", "export_enabled", False))
        self.sp_clean_exp.setValue(c.get_int("Cleanup", "export_days", 30))
        self.cb_clean_log.setChecked(c.get_bool("Cleanup", "logs_enabled", False))
        self.sp_clean_log.setValue(c.get_int("Cleanup", "log_days", 30))
        self.ed_org.setText(c.get("Support", "organization", ""))
        self.ed_eng.setText(c.get("Support", "engineer", ""))
        self.ed_phone.setText(c.get("Support", "phone", ""))

    def _set_combo(self, combo: QComboBox, data: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == data:
                combo.setCurrentIndex(i)
                return

    def save_settings_from_ui(self) -> None:
        c = self.cfg
        imp = self.ed_import.text().strip()
        exp = self.ed_export.text().strip()
        lg = self.ed_logdir.text().strip()
        # папки не должны совпадать/пересекаться (как в оригинале)
        folder_errors = validate_folders(imp, exp, lg)
        if folder_errors:
            QMessageBox.critical(self, "Папки", "\n".join(folder_errors))
            return
        if imp and is_network_path(imp):
            QMessageBox.warning(self, "Предупреждение", STR["network_path_warn"])
        c.set("Folders", "import_dir", imp)
        c.set("Folders", "export_dir", exp)
        c.set("Folders", "log_dir", lg)
        c.set("Folders", "ignore_ext", self.ed_ignore_ext.text().strip())
        c.set("Folders", "ignore_name", self.ed_ignore_name.text().strip())
        c.set("Folders", "recursive", self.cb_recursive.isChecked())
        c.set("Folders", "delay_sec", self.sp_delay.value())
        c.set("Dicom", "check_signature", self.cb_check_sig.isChecked())
        c.set("Encoding", "mode", self.cmb_encoding.currentData())
        c.set("PatientID", "auto_generate", self.cb_gen_pid.isChecked())
        c.set("UID", "new_study_if_bad", self.cb_uid_study.isChecked())
        c.set("UID", "new_series_if_bad", self.cb_uid_series.isChecked())
        c.set("UID", "new_sop_if_bad", self.cb_uid_sop.isChecked())
        c.set("Transliteration", "enabled", self.cb_translit.isChecked())
        c.set("Transliteration", "standard", self.cmb_translit.currentData())
        c.set("Anonymize", "enabled", self.cb_anon.isChecked())
        c.set("Anonymize", "keep_patient_id", self.cb_keep_id.isChecked())
        c.set("Anonymize", "keep_sex", self.cb_keep_sex.isChecked())
        c.set("Anonymize", "remove_private", self.cb_rm_private.isChecked())
        c.set("PACS", "local_aet", self.ed_local_aet.text().strip() or "DICOMBRIDGE")
        c.set("PACS", "remote_aet", self.ed_remote_aet.text().strip() or "PACS")
        c.set("PACS", "host", self.ed_host.text().strip() or "127.0.0.1")
        c.set("PACS", "port", self.sp_port.value())
        c.set("PACS", "timeout_sec", self.sp_timeout.value())
        c.set("PACS", "send_enabled", self.cb_send.isChecked())
        c.set("PACS", "echo_interval_min", self.sp_echo.value())
        c.set("Queue", "retry_interval_min", self.sp_retry.value())
        c.set("General", "log_level", self.cmb_log.currentText())
        c.set("General", "autostart", self.cb_autostart.isChecked())
        c.set("General", "minimize_to_tray", self.cb_tray.isChecked())
        c.set("Cleanup", "export_enabled", self.cb_clean_exp.isChecked())
        c.set("Cleanup", "export_days", self.sp_clean_exp.value())
        c.set("Cleanup", "logs_enabled", self.cb_clean_log.isChecked())
        c.set("Cleanup", "log_days", self.sp_clean_log.value())
        c.set("Support", "organization", self.ed_org.text().strip())
        c.set("Support", "engineer", self.ed_eng.text().strip())
        c.set("Support", "phone", self.ed_phone.text().strip())
        c.save()
        set_level(c.get("General", "log_level", "INFO"))
        set_autostart(self.cb_autostart.isChecked())
        # сохраняем правила из таблицы
        self._save_rules_from_ui()
        # перезапуск мониторинга новым конфигом + первичное сканирование
        self.restart_monitoring()
        self._start_timers()  # обновить интервалы
        QMessageBox.information(self, "Готово", "Настройки сохранены, мониторинг перезапущен.")

    def _pick_dir(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if d:
            edit.setText(d)
            if edit is self.ed_import and is_network_path(d):
                QMessageBox.warning(self, "Предупреждение", STR["network_path_warn"])

    # ================= правила =================
    def _load_rules_to_ui(self) -> None:
        rules = tags_mod.load_rules(self.cfg.rules_path())
        self.tbl_rules.setRowCount(0)
        for r in rules:
            self._append_rule_row(r.tag, r.vr, r.action, r.value)

    def _append_rule_row(self, tag="", vr="LO", action="replace", value="") -> None:
        row = self.tbl_rules.rowCount()
        self.tbl_rules.insertRow(row)
        self.tbl_rules.setItem(row, 0, QTableWidgetItem(tag))
        self.tbl_rules.setItem(row, 1, QTableWidgetItem(vr))
        self.tbl_rules.setItem(row, 2, QTableWidgetItem(action))
        self.tbl_rules.setItem(row, 3, QTableWidgetItem(value))

    def _rules_from_ui(self) -> list:
        out = []
        for row in range(self.tbl_rules.rowCount()):
            def _txt(col):
                it = self.tbl_rules.item(row, col)
                return it.text().strip() if it else ""
            out.append(tags_mod.Rule(
                tag=_txt(0), vr=_txt(1).upper(), action=_txt(2), value=_txt(3)))
        return out

    def _save_rules_from_ui(self) -> None:
        rules = self._rules_from_ui()
        # валидация: плохие правила подсвечиваем, но сохраняем (обработка их пропустит с логом)
        for r in rules:
            try:
                warns = tags_mod.validate_rule(r)
                for w in warns:
                    log.warning("правило %s: %s", r.tag, w)
            except ValueError as exc:
                log.error("некорректное правило %s: %s", r.tag, exc)
        tags_mod.save_rules(self.cfg.rules_path(), rules)

    def on_rule_add(self) -> None:
        self._append_rule_row("(0010,0010)", "PN", "replace", "")

    def on_rule_del(self) -> None:
        row = self.tbl_rules.currentRow()
        if row >= 0:
            self.tbl_rules.removeRow(row)

    def on_rule_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Импорт правил", "", "JSON (*.json)")
        if not path:
            return
        try:
            rules = tags_mod.load_rules(path)
            self.tbl_rules.setRowCount(0)
            for r in rules:
                self._append_rule_row(r.tag, r.vr, r.action, r.value)
            self._save_rules_from_ui()
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать: {exc}")

    def on_rule_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт правил", "rules.json", "JSON (*.json)")
        if not path:
            return
        try:
            tags_mod.save_rules(path, self._rules_from_ui())
            QMessageBox.information(self, "Готово", f"Правила сохранены: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить: {exc}")

    # ================= мониторинг =================
    def start_monitoring(self) -> None:
        imp = self.cfg.get("Folders", "import_dir", "").strip()
        if not imp:
            QMessageBox.warning(self, "Нет папки", "Укажите папку импорта в настройках.")
            return
        if not Path(imp).is_dir():
            QMessageBox.warning(self, "Нет папки", f"Папка не существует: {imp}")
            return
        if is_network_path(imp):
            QMessageBox.warning(self, "Предупреждение", STR["network_path_warn"])
        self.stop_monitoring(silent=True)
        rec = self.cfg.get_bool("Folders", "recursive", True)
        self.watcher = FolderWatcher(
            imp, recursive=rec, callback=self.processor.submit,
            ignore_exts=self.cfg.ignore_exts(),
            ignore_names=self.cfg.ignore_names(),
        )
        # сначала первичное сканирование (файлы, появившиеся в момент перезапуска),
        # затем запуск наблюдателя — ничего не потеряется
        for p in self.watcher.initial_scan():
            self.processor.submit(p)
        try:
            self.watcher.start()
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось запустить мониторинг: {exc}")
            return
        self._update_status()

    def stop_monitoring(self, silent: bool = False) -> None:
        if self.watcher is not None:
            try:
                self.watcher.stop()
            except Exception as exc:
                if not silent:
                    log.error("остановка мониторинга: %s", exc)
            self.watcher = None
        self._update_status()

    def restart_monitoring(self) -> None:
        running = self.watcher is not None
        self.stop_monitoring(silent=True)
        if running or self.cfg.get("Folders", "import_dir", "").strip():
            try:
                self.start_monitoring()
            except Exception as exc:
                log.error("перезапуск мониторинга: %s", exc)

    def _update_status(self) -> None:
        if self.watcher is not None:
            self.lbl_monitor.setText(f"● {t('status_running')}: {self.cfg.get('Folders', 'import_dir', '')}")
        else:
            self.lbl_monitor.setText(f"○ {t('status_stopped')}")

    # ================= PACS / очередь =================
    def on_test_echo(self) -> None:
        ok, msg = sender_from_config(self.cfg).test_echo()
        if ok:
            QMessageBox.information(self, "C-ECHO", msg)
        else:
            QMessageBox.warning(self, "C-ECHO", msg)
        self.lbl_pacs.setText(f"{'●' if ok else '○'} {msg}")
        self._was_online = ok

    def on_analyze(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите DICOM-файл", "", "DICOM (*.dcm *.dic *.*)")
        if not path:
            return
        mode = self.cmb_encoding.currentData()
        info = encoding_fix.analyze_file(path, mode)
        QMessageBox.information(
            self, "Анализ кодировки",
            f"Файл: {path}\n"
            f"Specific Character Set: {', '.join(info['charset'])}\n"
            f"Имя пациента (сейчас): {info['patient_before']}\n"
            f"Имя пациента (после): {info['patient_after']}\n\n"
            f"{info['message']}")

    def on_about(self) -> None:
        """Диалог «О программе» с контактами и кнопкой GitHub автора."""
        from config import APP_NAME
        c = self.cfg
        org = c.get("Support", "organization", "") or "—"
        eng = c.get("Support", "engineer", "") or "—"
        phone = c.get("Support", "phone", "") or "—"
        dlg = QDialog(self)
        dlg.setWindowTitle(f"О программе {APP_NAME}")
        try:
            dlg.setWindowIcon(QIcon(str(resource_path("assets/logo.png"))))
        except Exception:
            pass
        lay = QVBoxLayout(dlg)
        logo_lbl = QLabel()
        try:
            pm = QPixmap(str(resource_path("assets/logo.png")))
            if not pm.isNull():
                logo_lbl.setPixmap(pm.scaledToHeight(128, Qt.SmoothTransformation))
                logo_lbl.setAlignment(Qt.AlignCenter)
                lay.addWidget(logo_lbl)
        except Exception:
            pass
        info = QLabel(
            f"<b>{APP_NAME} {APP_VERSION}</b> — шлюз DICOM → PACS<br>"
            f"Профиль: {c.profile_name()}<br><br>"
            f"Обслуживание: {org}<br>Инженер: {eng}<br>Телефон: {phone}<br><br>"
            f"Поставляется «как есть» (as is), без гарантий. "
            f"Не является медицинским изделием.")
        info.setWordWrap(True)
        lay.addWidget(info)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        btn_row.addWidget(buttons)
        lay.addLayout(btn_row)
        dlg.exec()

    def _clear_dir_files(self, title: str, directory: str) -> None:
        d = Path(directory)
        if not d.is_dir():
            QMessageBox.warning(self, title, f"Папка не существует: {directory}")
            return
        ans = QMessageBox.question(
            self, title, f"Удалить ВСЕ файлы из {d}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        n = 0
        for p in d.rglob("*"):
            try:
                if p.is_file():
                    p.unlink()
                    n += 1
            except Exception as exc:
                log.error("очистка %s: %s", p, exc)
        log.info("%s: удалено файлов: %d", d, n)
        QMessageBox.information(self, title, f"Удалено файлов: {n}")

    def on_clear_export(self) -> None:
        self._clear_dir_files(
            "Очистка экспорта", self.cfg.get("Folders", "export_dir", ""))

    def on_clear_logs(self) -> None:
        self._clear_dir_files("Очистка логов", str(self.cfg.log_dir()))

    def on_bundle(self) -> None:
        """Сохранить zip-пакет диагностики для инженера поддержки."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Пакет для поддержки",
            f"support_{self.cfg.profile_name()}.zip", "ZIP (*.zip)")
        if not path:
            return
        try:
            out = create_support_bundle(self.cfg, self.error_queue, path)
            QMessageBox.information(self, "Готово", f"Пакет сохранён: {out}")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось собрать пакет: {exc}")

    def refresh_queue(self) -> None:
        try:
            items = self.error_queue.list_all()
        except Exception as exc:
            log.error("чтение очереди: %s", exc)
            return
        self.tbl_queue.setRowCount(0)
        for rec in items:
            row = self.tbl_queue.rowCount()
            self.tbl_queue.insertRow(row)
            tm = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["created"]))
            self.tbl_queue.setItem(row, 0, QTableWidgetItem(str(rec["id"])))
            self.tbl_queue.setItem(row, 1, QTableWidgetItem(str(rec["file_path"])))
            self.tbl_queue.setItem(row, 2, QTableWidgetItem(tm))
            self.tbl_queue.setItem(row, 3, QTableWidgetItem(str(rec.get("error", ""))[:200]))

    def on_retry_selected(self) -> None:
        row = self.tbl_queue.currentRow()
        if row < 0:
            return
        rid = int(self.tbl_queue.item(row, 0).text())
        fpath = self.tbl_queue.item(row, 1).text()
        ok, msg = self.processor.retry_record(rid, fpath)
        self.refresh_queue()
        QMessageBox.information(self, "Повтор", ("Успешно: " if ok else "Неудачно: ") + msg)

    def on_queue_delete(self) -> None:
        """Удалить выбранную запись из очереди ошибок."""
        row = self.tbl_queue.currentRow()
        if row < 0:
            return
        try:
            rid = int(self.tbl_queue.item(row, 0).text())
            self.error_queue.remove(rid)
            log.info("запись очереди удалена: id=%d", rid)
        except Exception as exc:
            log.error("удаление из очереди: %s", exc)
        self.refresh_queue()

    def on_retry_all(self) -> None:
        res = self.processor.retry_all()
        self.refresh_queue()
        QMessageBox.information(
            self, "Повторная отправка",
            f"Всего: {res['total']}, успешно: {res['ok']}, неудачно: {res['failed']}")

    # ================= таймеры: echo / retry / cleanup =================
    def _start_timers(self) -> None:
        for attr in ("_echo_timer", "_retry_timer", "_clean_timer"):
            old = getattr(self, attr, None)
            if old is not None:
                try:
                    old.stop()
                except Exception:
                    pass
        echo_min = max(1, self.cfg.get_int("PACS", "echo_interval_min", 20))
        retry_min = max(1, self.cfg.get_int("Queue", "retry_interval_min", 5))
        self._echo_timer = QTimer(self)
        self._echo_timer.timeout.connect(self._periodic_echo)
        self._echo_timer.start(echo_min * 60 * 1000)
        self._retry_timer = QTimer(self)
        self._retry_timer.timeout.connect(self._periodic_retry)
        self._retry_timer.start(retry_min * 60 * 1000)
        self._clean_timer = QTimer(self)
        self._clean_timer.timeout.connect(self._periodic_cleanup)
        self._clean_timer.start(60 * 60 * 1000)  # раз в час

    def _periodic_echo(self) -> None:
        if not self.cfg.get_bool("PACS", "send_enabled", True):
            return
        ok, msg = sender_from_config(self.cfg).test_echo()
        self.lbl_pacs.setText(f"{'●' if ok else '○'} {msg} ({time.strftime('%H:%M')})")
        # после восстановления связи предлагаем переслать накопившееся
        if ok and self._was_online is False:
            n = self.error_queue.count_pending()
            if n > 0:
                ans = QMessageBox.question(
                    self, "Связь восстановлена",
                    t("queue_offer_resend", n=n))
                if ans == QMessageBox.Yes:
                    self.on_retry_all()
        self._was_online = ok

    def _periodic_retry(self) -> None:
        if not self.cfg.get_bool("PACS", "send_enabled", True):
            return
        if self.error_queue.count_pending() == 0:
            return
        ok, _ = sender_from_config(self.cfg).test_echo()
        if ok:
            res = self.processor.retry_all()
            self.refresh_queue()
            log.info("автоповтор очереди: %s", res)

    def _periodic_cleanup(self) -> None:
        if self.cfg.get_bool("Cleanup", "export_enabled", False):
            cleanup_mod.cleanup_old_files(
                self.cfg.get("Folders", "export_dir", ""),
                self.cfg.get_int("Cleanup", "export_days", 30))
        if self.cfg.get_bool("Cleanup", "logs_enabled", False):
            cleanup_mod.cleanup_old_logs(
                str(self.cfg.log_dir()),
                self.cfg.get_int("Cleanup", "log_days", 30))

    # ================= события процессора / статистика =================
    def _on_proc_event(self, event: dict) -> None:
        # вызывается из фонового потока — перебрасываем в GUI-поток
        QTimer.singleShot(0, lambda: self.refresh_queue())

    def _on_stats(self, snap: dict) -> None:
        QTimer.singleShot(0, lambda: self._apply_stats(snap))

    def _apply_stats(self, snap: dict) -> None:
        self.lbl_processed.setText(str(snap.get("processed", 0)))
        self.lbl_sent.setText(str(snap.get("sent", 0)))
        self.lbl_queued.setText(str(snap.get("queued", 0)))
        self.lbl_errors.setText(str(snap.get("errors", 0)))

    # ================= лог =================
    def _load_recent_log(self) -> None:
        try:
            lf = self.cfg.log_dir() / "dicombridge.log"
            if lf.exists():
                lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
                for ln in lines:
                    self._store_log_line(ln)
                self._refilter_log()
        except Exception:
            pass

    def _store_log_line(self, msg: str) -> None:
        self._log_lines.append(msg)
        if len(self._log_lines) > 2000:
            self._log_lines = self._log_lines[-2000:]

    @Slot(str)
    def _append_log_line(self, msg: str) -> None:
        self._store_log_line(msg)
        filt = self.cmb_log_filter.currentText()
        if filt != "ALL" and f"[{filt}]" not in msg and f"[{filt.lower()}]" not in msg:
            # WARN покрывает WARNING
            if not (filt == "WARN" and "[WARNING]" in msg):
                return
        self.txt_log.append(msg)
        sb = self.txt_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _refilter_log(self) -> None:
        filt = self.cmb_log_filter.currentText()
        self.txt_log.clear()
        for ln in self._log_lines[-500:]:
            if filt == "ALL" or f"[{filt}]" in ln or (filt == "WARN" and "[WARNING]" in ln):
                self.txt_log.append(ln)

    # ================= трей / закрытие =================
    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()

    def closeEvent(self, event) -> None:
        if self.cfg.get_bool("General", "minimize_to_tray", True):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "DicomBridge", "Программа свёрнута в трей и продолжает работать.")
        else:
            self._shutdown()
            event.accept()

    def _shutdown(self) -> None:
        try:
            self.stop_monitoring(silent=True)
        except Exception:
            pass
        try:
            self.processor.stop()
        except Exception:
            pass
