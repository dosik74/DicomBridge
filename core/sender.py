"""Отправка DICOM-файлов на PACS (C-STORE) и проверка связи (C-ECHO).

Ключевой урок из полевых логов: предлагать серверу сотни контекстов
(все SOP-классы x все синтаксисы) нельзя — строгие SCP отклоняют нужные
контексты. Поэтому на каждый файл предлагается ТОЛЬКО его SOP-класс:
родной Transfer Syntax файла + несжатые запасные (Explicit/Implicit LE).
Если сервер не принял родной (например, JPEG-LS), а несжатый принял —
пиксели автоматически транскодируются в памяти (нужны numpy и плагины
декодирования, см. requirements.txt).
"""

import logging
import os

log = logging.getLogger("sender")

EXPLICIT_LE = "1.2.840.10008.1.2.1"
IMPLICIT_LE = "1.2.840.10008.1.2"
EXPLICIT_BE = "1.2.840.10008.1.2.2"
VERIFICATION_SOP = "1.2.840.10008.1.1"

UNCOMPRESSED_TS = (EXPLICIT_LE, IMPLICIT_LE)

TS_NAMES = {
    EXPLICIT_LE: "Explicit LE",
    IMPLICIT_LE: "Implicit LE",
    EXPLICIT_BE: "Explicit BE",
    "1.2.840.10008.1.2.4.50": "JPEG Baseline",
    "1.2.840.10008.1.2.4.51": "JPEG Extended",
    "1.2.840.10008.1.2.4.57": "JPEG Lossless NH",
    "1.2.840.10008.1.2.4.70": "JPEG Lossless",
    "1.2.840.10008.1.2.4.80": "JPEG-LS Lossless",
    "1.2.840.10008.1.2.4.81": "JPEG-LS Lossy",
    "1.2.840.10008.1.2.4.90": "JPEG 2000 Lossless",
    "1.2.840.10008.1.2.4.91": "JPEG 2000 Lossy",
    "1.2.840.10008.1.2.5": "RLE Lossless",
}


def ts_name(uid: str) -> str:
    """Человекочитаемое имя Transfer Syntax."""
    return TS_NAMES.get(str(uid), str(uid))


def _make_echo_ae(local_aet: str):
    """Минимальный AE для C-ECHO (только Verification)."""
    from pynetdicom import AE
    ae = AE(ae_title=local_aet or "DICOMBRIDGE")
    ae.add_requested_context(VERIFICATION_SOP)
    return ae


def _make_store_ae(local_aet: str, sop_uid: str, file_ts: str):
    """AE с точечным контекстом: родной TS файла + несжатые запасные."""
    from pynetdicom import AE
    proposals = []
    for ts in (file_ts, EXPLICIT_LE, IMPLICIT_LE):
        if ts and ts not in proposals:
            proposals.append(ts)
    ae = AE(ae_title=local_aet or "DICOMBRIDGE")
    ae.add_requested_context(sop_uid, transfer_syntax=proposals)
    return ae


def accepted_transfer_syntaxes(assoc, sop_uid: str) -> list:
    """Какие Transfer Syntax сервер принял для данного SOP-класса."""
    out = []
    for pc in getattr(assoc, "accepted_contexts", []) or []:
        try:
            if str(getattr(pc, "abstract_syntax", "")) != sop_uid:
                continue
            ts = getattr(pc, "transfer_syntax", None)
            if isinstance(ts, (list, tuple)):
                out.extend(str(t) for t in ts)
            elif ts:
                out.append(str(ts))
        except Exception:
            continue
    return out


def choose_send_ts(file_ts: str, accepted: list) -> str | None:
    """Выбрать TS для отправки: родной, иначе несжатый. Иначе None."""
    if file_ts in accepted:
        return file_ts
    for ts in UNCOMPRESSED_TS:
        if ts in accepted:
            return ts
    return None


def transcode_dataset(ds, target_ts: str):
    """Перевести датасет в целевой несжатый TS (возвращает НОВЫЙ датасет).

    pynetdicom при отправке смотрит на original_encoding, выставленный
    при чтении файла, поэтому простого флипа флагов недостаточно —
    делаем круговой save→read, который материализует нужную кодировку.
    """
    from copy import deepcopy
    from io import BytesIO
    from pydicom import dcmread
    from pydicom.uid import UID

    out = deepcopy(ds)
    out.is_implicit_VR = (target_ts == IMPLICIT_LE)
    out.is_little_endian = True
    try:
        out.file_meta.TransferSyntaxUID = UID(target_ts)
    except Exception:
        pass
    try:
        buf = BytesIO()
        out.save_as(buf, enforce_file_format=True)
        buf.seek(0)
        return dcmread(buf, force=True)
    except Exception:
        return out  # запасной вариант: хотя бы флаги выставлены


def decompress_dataset(ds):
    """Распаковать сжатые пиксели. Бросает RuntimeError с подсказкой."""
    try:
        ds.decompress()
    except Exception as exc:
        raise RuntimeError(
            "не удалось распаковать пиксели "
            f"({type(exc).__name__}: {exc}). "
            "Установите: pip install numpy pillow pylibjpeg pylibjpeg-libjpeg "
            "pylibjpeg-openjpeg pyjpegls"
        )


def file_transfer_syntax(ds, path: str) -> str:
    """Определить TS файла (по file_meta, иначе Explicit LE)."""
    try:
        ts = str(getattr(getattr(ds, "file_meta", None),
                         "TransferSyntaxUID", "") or "")
        if ts:
            return ts
    except Exception:
        pass
    return EXPLICIT_LE


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
            ae = _make_echo_ae(self.local_aet)
            assoc = ae.associate(
                self.host, self.port,
                ae_title=self.remote_aet,
                max_pdu=16382,
            )
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

        sop_uid = str(getattr(ds, "SOPClassUID", "") or "")
        if not sop_uid:
            return False, "в файле нет SOP Class UID"
        file_ts = file_transfer_syntax(ds, path)

        try:
            ae = _make_store_ae(self.local_aet, sop_uid, file_ts)
            assoc = ae.associate(self.host, self.port, ae_title=self.remote_aet)
            if not assoc.is_established:
                return False, f"ассоциация отклонена {self.host}:{self.port}"
            try:
                accepted = accepted_transfer_syntaxes(assoc, sop_uid)
                send_ts = choose_send_ts(file_ts, accepted)
                if send_ts is None:
                    acc = ", ".join(ts_name(t) for t in accepted) or "ничего"
                    return False, (
                        f"PACS не принял SOP {sop_uid} ни в одном синтаксисе "
                        f"(файл: {ts_name(file_ts)}; сервер согласился: {acc}). "
                        f"Проверьте настройки SCP и разрешение для AE Title "
                        f"'{self.local_aet}'."
                    )
                note = ""
                if send_ts != file_ts:
                    if file_ts not in UNCOMPRESSED_TS + (EXPLICIT_BE,):
                        try:
                            decompress_dataset(ds)
                        except RuntimeError as exc:
                            return False, str(exc)
                    ds = transcode_dataset(ds, send_ts)
                    note = f" (транскодирован {ts_name(file_ts)} → {ts_name(send_ts)})"
                    log.info("транскодирован для отправки %s: %s -> %s",
                             path, ts_name(file_ts), ts_name(send_ts))
                assoc.dimse_timeout = self.timeout
                try:
                    status = assoc.send_c_store(ds)
                except Exception as exc:
                    return False, (
                        f"сервер отклонил контекст ({exc}). "
                        f"Проверьте настройки SCP для AE Title '{self.local_aet}'."
                    )
            finally:
                assoc.release()
        except Exception as exc:
            log.error("C-STORE %s: %s", path, exc)
            return False, f"сетевая ошибка: {exc}"

        code = getattr(status, "Status", None)
        if status is not None and code == 0x0000:
            return True, f"C-STORE успешен (статус 0x0000){note}"
        if status is None:
            return False, "нет ответа на C-STORE (таймаут)"
        hexcode = f"0x{code:04X}" if isinstance(code, int) else str(code)
        ok = code in (0x0000, 0xB000)  # B000 — warning, но сохранено
        msg = f"C-STORE статус {hexcode}{note}"
        if not ok:
            log.warning("%s: %s", path, msg)
        return ok, msg
