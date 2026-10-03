"""Отправка DICOM-файлов на PACS (C-STORE) и проверка связи (C-ECHO).

Используются Presentation Contexts для основных SOP-классов
(CR, DX, MG, CT, MR, US, XA, ECG и др.) и набор Transfer Syntaxes,
включая сжатые (JPEG, JPEG-LS, JPEG 2000, RLE).
"""

import logging
import os

log = logging.getLogger("sender")

# Основные Storage SOP-классы (остальные подхватываются динамически из файла)
STORAGE_SOP_CLASSES = [
    "1.2.840.10008.5.1.4.1.1.1",    # CR
    "1.2.840.10008.5.1.4.1.1.1.1",  # DX
    "1.2.840.10008.5.1.4.1.1.1.2",  # DX for Presentation
    "1.2.840.10008.5.1.4.1.1.1.3",  # DX for Processing
    "1.2.840.10008.5.1.4.1.1.2",    # CT
    "1.2.840.10008.5.1.4.1.1.2.1",  # Enhanced CT
    "1.2.840.10008.5.1.4.1.1.3",    # US MF (retired)
    "1.2.840.10008.5.1.4.1.1.3.1",  # US MF
    "1.2.840.10008.5.1.4.1.1.4",    # MR
    "1.2.840.10008.5.1.4.1.1.4.1",  # Enhanced MR
    "1.2.840.10008.5.1.4.1.1.5",    # NM (retired)
    "1.2.840.10008.5.1.4.1.1.6.1",  # US Image
    "1.2.840.10008.5.1.4.1.1.7",    # SC
    "1.2.840.10008.5.1.4.1.1.7.1",  # SC Color (retired)
    "1.2.840.10008.5.1.4.1.1.7.2",  # SC Grayscale (retired)
    "1.2.840.10008.5.1.4.1.1.7.4",  # SC Photometric Interpretation
    "1.2.840.10008.5.1.4.1.1.9.1.1",  # 12-lead ECG
    "1.2.840.10008.5.1.4.1.1.9.1.2",  # General ECG
    "1.2.840.10008.5.1.4.1.1.9.1.3",  # Ambulatory ECG
    "1.2.840.10008.5.1.4.1.1.11.1",   # XA
    "1.2.840.10008.5.1.4.1.1.12.1",   # XRF
    "1.2.840.10008.5.1.4.1.1.12.2",   # XA/XRF Grayscale Softcopy
    "1.2.840.10008.5.1.4.1.1.20",     # NM
    "1.2.840.10008.5.1.4.1.1.66",     # Raw Data
    "1.2.840.10008.5.1.4.1.1.77.1.1",  # VL Endoscopic
    "1.2.840.10008.5.1.4.1.1.77.1.2",  # VL Microscopic
    "1.2.840.10008.5.1.4.1.1.77.1.4",  # VL Photographic
    "1.2.840.10008.5.1.4.1.1.128",    # PET
    "1.2.840.10008.5.1.4.1.1.130",    # Enhanced PET
    "1.2.840.10008.5.1.4.1.1.481.1",  # RT Image
    "1.2.840.10008.5.1.4.1.1.104.1",  # Encapsulated PDF
    "1.2.840.10008.5.1.4.1.1.104.2",  # Encapsulated CDA
]

TRANSFER_SYNTAXES = [
    "1.2.840.10008.1.2.1",      # Explicit VR Little Endian
    "1.2.840.10008.1.2",        # Implicit VR Little Endian
    "1.2.840.10008.1.2.2",      # Explicit VR Big Endian (retired)
    "1.2.840.10008.1.2.4.50",   # JPEG Baseline
    "1.2.840.10008.1.2.4.51",   # JPEG Extended
    "1.2.840.10008.1.2.4.57",   # JPEG Lossless NH
    "1.2.840.10008.1.2.4.70",   # JPEG Lossless
    "1.2.840.10008.1.2.4.80",   # JPEG-LS Lossless
    "1.2.840.10008.1.2.4.81",   # JPEG-LS Lossy
    "1.2.840.10008.1.2.4.90",   # JPEG 2000 Lossless
    "1.2.840.10008.1.2.4.91",   # JPEG 2000 Lossy
    "1.2.840.10008.1.2.5",      # RLE Lossless
]


def _make_ae(local_aet: str):
    from pynetdicom import AE
    ae = AE(ae_title=local_aet or "DICOMBRIDGE")
    ae.add_requested_context("1.2.840.10008.1.1")  # Verification SOP Class
    for sop in STORAGE_SOP_CLASSES:
        try:
            ae.add_requested_context(sop, transfer_syntax=TRANSFER_SYNTAXES)
        except Exception:
            pass
    return ae


class PacsSender:
    """Отправка файлов на PACS-сервер."""

    def __init__(self, local_aet="DICOMBRIDGE", remote_aet="PACS",
                 host="127.0.0.1", port=11112, timeout=10):
        self.local_aet = (local_aet or "DICOMBRIDGE")[:16].strip() or "DICOMBRIDGE"
        self.remote_aet = remote_aet or "PACS"
        self.host = host or "127.0.0.1"
        self.port = int(port or 11112)
        self.timeout = int(timeout or 10)

    def test_echo(self) -> tuple[bool, str]:
        """Проверка связи C-ECHO. Возвращает (успех, сообщение)."""
        try:
            ae = _make_ae(self.local_aet)
            assoc = ae.associate(
                self.host, self.port,
                ae_title=self.remote_aet,
                max_pdu=16382,
            )
            # pynetdicom: таймауты через ae.connection_timeout / dimse_timeout
            if not assoc.is_established:
                return False, f"Нет ассоциации с {self.host}:{self.port} (проверьте IP, порт, AE Title)"
            try:
                assoc.dimse_timeout = self.timeout
                status = assoc.send_c_echo()
            finally:
                assoc.release()
            if status and getattr(status, "Status", None) == 0x0000:
                return True, f"C-ECHO успешен ({self.host}:{self.port}, {self.remote_aet})"
            return False, f"C-ECHO отклонён, статус: {getattr(status, 'Status', status)}"
        except Exception as exc:
            log.error("C-ECHO ошибка: %s", exc)
            return False, f"Ошибка соединения: {exc}"

    def send_file(self, path: str) -> tuple[bool, str]:
        """Отправить один файл C-STORE. Возвращает (успех, сообщение со статусом)."""
        from pydicom import dcmread

        if not os.path.exists(path):
            return False, "файл не найден (возможно удалён автоочисткой)"
        try:
            ds = dcmread(path, force=True)
        except Exception as exc:
            return False, f"не удалось прочитать DICOM: {exc}"

        sop_uid = str(getattr(ds, "SOPClassUID", "")) or None
        if not sop_uid:
            return False, "в файле нет SOP Class UID"
        try:
            ae = _make_ae(self.local_aet)
            # контекст именно под SOP-класс этого файла (плюс общий список)
            try:
                ae.add_requested_context(sop_uid, transfer_syntax=TRANSFER_SYNTAXES)
            except Exception:
                pass
            # кастомный SOP-класс, если UID нет в стандартном списке
            assoc = ae.associate(self.host, self.port, ae_title=self.remote_aet)
            if not assoc.is_established:
                return False, f"ассоциация отклонена {self.host}:{self.port}"
            try:
                assoc.dimse_timeout = self.timeout
                status = assoc.send_c_store(ds)
            finally:
                assoc.release()
        except Exception as exc:
            log.error("C-STORE %s: %s", path, exc)
            return False, f"сетевая ошибка: {exc}"

        code = getattr(status, "Status", None)
        if code in (0x0000, None) and status is not None and getattr(status, "Status", None) == 0x0000:
            return True, "C-STORE успешен (статус 0x0000)"
        if status is None:
            return False, "нет ответа на C-STORE (таймаут)"
        hexcode = f"0x{code:04X}" if isinstance(code, int) else str(code)
        ok = code in (0x0000, 0xB000)  # B000 — warning, но сохранено
        msg = f"C-STORE статус {hexcode}"
        if not ok:
            log.warning("%s: %s", path, msg)
        return ok, msg
