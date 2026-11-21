import struct
import sys

def u32(buf, off):
    return next(iter(struct.unpack_from("<I", buf, off)))

def u16(buf, off):
    return next(iter(struct.unpack_from("<H", buf, off)))

def align_up(val, alignment):
    rem = val % alignment
    return val if rem == 0 else val + (alignment - rem)

def pack_jdlz_raw(raw_bytes):
    # Валідний 16-байтний заголовок EA JDLZ (0x00001002) + потік літералів (flags1 = 0x00 на кожні 8 байт)
    unpacked_len = len(raw_bytes)
    body = bytearray()
    pos = 0
    while pos < unpacked_len:
        body.append(0x00)
        chunk = raw_bytes[pos : pos + 8]
        body.extend(chunk)
        pos += 8
    total_len = 16 + len(body)
    header = struct.pack("<4sIII", b"JDLZ", 0x00001002, unpacked_len, total_len)
    return header + bytes(body)

def convert_textures_file(pc_path, out_path, donor_android_path=None):
    with open(pc_path, "rb") as f:
        pc = bytes(f.read())

    # 1. Знаходимо головний заголовок 0xB3310000 у PC
    h_pos = pc.find(b'\x00\x00\x31\xB3')
    h_size = u32(pc, h_pos + 4)

    subchunks = {}
    sp = h_pos + 8
    while sp + 8 <= h_pos + 8 + h_size:
        cid, csz = struct.unpack_from("<II", pc, sp)
        if cid != 0:
            subchunks[cid] = bytearray(pc[sp+8 : sp+8+csz])
        sp += 8 + csz

    # 2. Чистий заголовок 0x33310001 для Xbox
    if donor_android_path:
        with open(donor_android_path, "rb") as df:
            donor = df.read()
        dp1 = donor.find(b'\x01\x00\x31\x33')
        if dp1 != -1:
            subchunks[0x33310001] = bytearray(donor[dp1+8 : dp1+8+124])
    else:
        c1 = bytearray(124)
        struct.pack_into("<I", c1, 0, 3)
        c1[4:16] = b"CARTEXTURES\x00"
        c1[36:77] = b"Global\\Art\\Cars\\FOCUS\\FOCUS_Textures.tpk\x00"
        subchunks[0x33310001] = c1

    # 3. Конвертуємо 0x33310005 з 32-байтних записів PC у 60-байтні записи Xbox NV2A
    c5_pc = subchunks[0x33310005]
    num_tex = len(c5_pc) // 32
    c5_xbox = bytearray()
    tex_formats = []

    for i in range(num_tex):
        rec = c5_pc[i*32 : (i+1)*32]
        fourcc = bytes(rec[20:24])
        xrec = bytearray(60)
        if fourcc == b"DXT1":
            struct.pack_into("<IIII", xrec, 0, 0, 0x00000302, 0x00000303, 0x0000000C)
            tex_formats.append("DXT1")
        elif fourcc == b"DXT3":
            struct.pack_into("<IIII", xrec, 0, 1, 0x00000302, 0x00000303, 0x0000000E)
            tex_formats.append("DXT3")
        elif fourcc == b"DXT5":
            struct.pack_into("<IIII", xrec, 0, 1, 0x00000302, 0x00000303, 0x0000000F)
            tex_formats.append("DXT5")
        else:
            struct.pack_into("<IIII", xrec, 0, 1, 0x00000302, 0x00000303, 0x00000006)
            tex_formats.append("RGBA")
        c5_xbox.extend(xrec)

    subchunks[0x33310005] = c5_xbox

    # 4. Витягуємо кожну текстуру з PC 0x33310003 та пакуємо в JDLZ для Xbox
    c3 = subchunks[0x33310003]
    c4 = subchunks[0x33310004]

    pc_tex_by_hash = {}
    for i in range(len(c3) // 20):
        off3 = i * 20
        thash, old_abs_off, tex_sz, flags = struct.unpack_from("<IIII", c3, off3)
        raw_data = pc[old_abs_off : old_abs_off + tex_sz]
        jdlz_blob = raw_data if raw_data[:4] == b"JDLZ" else pack_jdlz_raw(raw_data)
        pc_tex_by_hash[thash] = (jdlz_blob, len(raw_data))

    # Будуємо 0x33320001 з падінгом 0x11 на початку (120 байт, як у оригіналі Android)
    pix_body = bytearray(b'\x11' * 120)
    jdlz_loc_by_hash = {}
    unpacked_off_cursor = 0

    for i in range(len(c3) // 20):
        off3 = i * 20
        thash = u32(c3, off3)
        if thash in pc_tex_by_hash:
            jdlz_blob, raw_len = pc_tex_by_hash[thash]
            rel_start = len(pix_body)
            pix_body.extend(jdlz_blob)
            # Вирівнюємо кожен JDLZ-блок по 4 байтах
            rem4 = len(pix_body) % 4
            if rem4 != 0:
                pix_body.extend(b'\x00' * (4 - rem4))
            jdlz_loc_by_hash[thash] = (rel_start, len(jdlz_blob), unpacked_off_cursor, raw_len)
            unpacked_off_cursor += raw_len

    # 5. Розраховуємо точне 128-байтне зміщення старту 0x33320001
    h_inner = bytearray()
    for cid in (0x33310001, 0x33310002, 0x33310003, 0x33310004, 0x33310005):
        if cid in subchunks:
            h_inner.extend(struct.pack("<II", cid, len(subchunks[cid])))
            h_inner.extend(subchunks[cid])

    after_header_pos = 16 + 8 + len(h_inner)
    pix_chunk_start = align_up(after_header_pos, 128)
    if pix_chunk_start - after_header_pos < 8:
        pix_chunk_start += 128
    pad_len = pix_chunk_start - after_header_pos
    pix_data_abs_start = pix_chunk_start + 8

    # 6. Оновлюємо 0x33310003 (абсолютне зміщення JDLZ, розмір JDLZ, прапорець 0x100)
    for i in range(len(c3) // 20):
        off3 = i * 20
        thash = u32(c3, off3)
        if thash in jdlz_loc_by_hash:
            rel_start, jdlz_len, _, _ = jdlz_loc_by_hash[thash]
            struct.pack_into("<III", c3, off3 + 4, pix_data_abs_start + rel_start, jdlz_len, 0x00000100)

    # Оновлюємо 0x33310004 (mip0_size, log2, міпмапи, прапорці)
    for i in range(num_tex):
        off4 = i * 124
        w = u16(c4, off4 + 68)
        h = u16(c4, off4 + 70)
        fmt = tex_formats[i]
        # Розмір базового міпмапа (mip0_size) на +64
        mip0_sz = (w * h) // 2 if fmt == "DXT1" else (w * h)
        struct.pack_into("<I", c4, off4 + 64, mip0_sz)
        c4[off4 + 74] = 0x22
        c4[off4 + 75] = 0x00
        # Кількість рівнів міпмапів (наприклад 9 для 256x256, якщо було 0)
        if c4[off4 + 78] == 0:
            mips = max(w, h).bit_length()
            c4[off4 + 78] = mips

    # Перезбираємо h_inner
    h_inner = bytearray()
    for cid in (0x33310001, 0x33310002, 0x33310003, 0x33310004, 0x33310005):
        if cid in subchunks:
            h_inner.extend(struct.pack("<II", cid, len(subchunks[cid])))
            h_inner.extend(subchunks[cid])

    # 7. Збираємо фінальний файл для Android/Xbox
    out = bytearray()
    out.extend(struct.pack("<II", 0xB3300000, 0))
    out.extend(b'\x00' * 8)
    out.extend(struct.pack("<II", 0xB3310000, len(h_inner)))
    out.extend(h_inner)

    out.extend(struct.pack("<II", 0, pad_len - 8))
    out.extend(b'\x00' * (pad_len - 8))

    out.extend(struct.pack("<II", 0x33320001, len(pix_body)))
    out.extend(pix_body)

    struct.pack_into("<I", out, 4, len(out) - 8)

    with open(out_path, "wb") as f:
        f.write(out)
    print(f"Готово! Запаковано в JDLZ та конвертовано {num_tex} текстур ({', '.join(sorted(set(tex_formats)))}). Розмір: {len(out)} байт.")

if __name__ == "__main__":
    args = list(sys.argv)
    args.pop(0)
    if len(args) < 2:
        print("Використання: python3 pc2xbox_tex.py <TEXTURES_pc.BIN> <TEXTURES.BIN> [TEXTURES_android_donor.BIN]")
        sys.exit(1)
    pc_in = args.pop(0)
    xbox_out = args.pop(0)
    donor_in = args.pop(0) if args else None
    convert_textures_file(pc_in, xbox_out, donor_in)
