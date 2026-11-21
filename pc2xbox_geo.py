import struct
import sys

def u32(buf, off):
    return next(iter(struct.unpack_from("<I", buf, off)))

def align_up(val, alignment):
    rem = val % alignment
    return val if rem == 0 else val + (alignment - rem)

def make_ea_padding(cur_abs, alignment):
    aligned_abs = align_up(cur_abs, alignment)
    pad = aligned_abs - cur_abs
    if pad == 0:
        return b""
    while pad < 8:
        pad += alignment
    return struct.pack("<II", 0, pad - 8) + (b"\x00" * (pad - 8))

def count_11_pad_4(buf):
    p = 0
    while p + 4 <= len(buf) and buf[p:p+4] == b'\x11\x11\x11\x11':
        p += 4
    return p

def pad_11_to(cur_abs_data_start, alignment):
    rem = cur_abs_data_start % alignment
    return b"" if rem == 0 else (b"\x11" * (alignment - rem))

def convert_mesh_container(pc_mesh_bytes, mesh_abs_start):
    pos = 0
    chunks = {}
    while pos + 8 <= len(pc_mesh_bytes):
        cid, csize = struct.unpack_from("<II", pc_mesh_bytes, pos)
        if cid != 0:
            chunks[cid] = pc_mesh_bytes[pos+8 : pos+8+csize]
        pos += 8 + csize

    if 0x00134900 not in chunks or 0x00134B01 not in chunks:
        return pc_mesh_bytes

    pc_vb = chunks[0x00134B01]
    vb_raw = pc_vb[count_11_pad_4(pc_vb):]
    num_verts = len(vb_raw) // 36

    pc_mb = chunks[0x00134B02]
    mb_raw = pc_mb[count_11_pad_4(pc_mb):]
    num_mats = len(mb_raw) // 60

    pc_ib = chunks[0x00134B03]
    ib_raw = pc_ib[count_11_pad_4(pc_ib):]

    out = bytearray()

    # 1. Заголовок 0x00134B00 (вирівнювання по 16 байтах)
    cur_data_abs = mesh_abs_start + len(out) + 8
    b00_pad = pad_11_to(cur_data_abs, 16)
    b00_body = bytearray(52)
    struct.pack_into("<IHHHI", b00_body, 0, 0x0000000F, 0x4180, num_verts, num_mats, 0x00000024)
    b00_full = b00_pad + bytes(b00_body)
    out.extend(struct.pack("<II", 0x00134B00, len(b00_full)))
    out.extend(b00_full)

    # 2. Вершини 0x00134B01 (вирівнювання по 128 байтах під кеш GPU Xbox)
    cur_data_abs = mesh_abs_start + len(out) + 8
    vb_pad = pad_11_to(cur_data_abs, 128)
    xbox_vb = bytearray(vb_pad)
    for i in range(num_verts):
        v_off = i * 36
        xbox_vb.extend(vb_raw[v_off : v_off+12])      # XYZ
        xbox_vb.extend(vb_raw[v_off+24 : v_off+36])   # Color + UV
        xbox_vb.extend(vb_raw[v_off+12 : v_off+24])   # Normal
    rem_vb = len(vb_raw) % 36
    if rem_vb > 0:
        xbox_vb.extend(vb_raw[-rem_vb:])
    out.extend(struct.pack("<II", 0x00134B01, len(xbox_vb)))
    out.extend(xbox_vb)

    # 3. Матеріали 0x00134B02 (точна 64-байтна структура Xbox)
    cur_data_abs = mesh_abs_start + len(out) + 8
    mb_pad = pad_11_to(cur_data_abs, 16)
    xbox_mb = bytearray(mb_pad)
    for i in range(num_mats):
        m_off = i * 60
        rec = mb_raw[m_off : m_off+60]
        xbox_rec = bytearray(64)
        xbox_rec[0:12]  = rec[0:12]     # bbox1
        xbox_rec[12:24] = rec[16:28]    # bbox2
        xbox_rec[24:28] = rec[12:16]    # num_indices
        xbox_rec[28:32] = rec[28:32]    # mat_id
        xbox_rec[32:36] = rec[56:60]    # flags
        xbox_rec[36:40] = rec[52:56]    # index_offset
        xbox_rec[40:44] = b'\x00\x00\x00\x00'
        xbox_rec[44:48] = rec[32:36]    # tex_id
        xbox_mb.extend(xbox_rec)
    out.extend(struct.pack("<II", 0x00134B02, len(xbox_mb)))
    out.extend(xbox_mb)

    # 4. Індекси 0x00134B03 (вирівнювання по 16 байтах)
    cur_data_abs = mesh_abs_start + len(out) + 8
    ib_pad = pad_11_to(cur_data_abs, 16)
    xbox_ib = ib_pad + ib_raw
    out.extend(struct.pack("<II", 0x00134B03, len(xbox_ib)))
    out.extend(xbox_ib)

    return bytes(out)

def convert_part_container(part_raw, part_abs_start):
    p_inner = bytearray()
    pos = 8
    while pos + 8 <= len(part_raw):
        cid, csize = struct.unpack_from("<II", part_raw, pos)
        cdata = part_raw[pos+8 : pos+8+csize]
        if cid == 0x80134100:
            mesh_abs_start = part_abs_start + 8 + len(p_inner) + 8
            cdata = convert_mesh_container(cdata, mesh_abs_start)
        p_inner.extend(struct.pack("<II", cid, len(cdata)))
        p_inner.extend(cdata)
        pos += 8 + csize
    return struct.pack("<II", 0x80134010, len(p_inner)) + bytes(p_inner)

def patch_xbox_header_info(header_info_bytes):
    b = bytearray(header_info_bytes)
    # Патчимо шлях ..\PC\ -> ..\XBOX\
    idx = b.find(b"..\\PC\\")
    if idx != -1:
        field = bytes(b[idx : idx + 56])
        new_field = field.replace(b"..\\PC\\", b"..\\XBOX\\", 1)[:56].ljust(56, b'\x00')
        b[idx : idx + 56] = new_field
    return bytes(b)

def convert_geometry_file(pc_path, donor_android_path, out_path):
    with open(pc_path, "rb") as f:
        pc_data = bytes(f.read())
    with open(donor_android_path, "rb") as f:
        donor = bytes(f.read())

    pc_main_end = 8 + u32(pc_data, 4)

    # 1. Будуємо заголовок 0x80134001 з ВАШОГО файлу NFS-CarToolkit (зберігаємо всі ваші деталі та обвіси!)
    pc_h_pos = pc_data.find(b'\x01\x40\x13\x80')
    pc_h_size = u32(pc_data, pc_h_pos + 4)

    h_inner = bytearray()
    h_pos = pc_h_pos + 8
    h_end = pc_h_pos + 8 + pc_h_size
    while h_pos + 8 <= h_end:
        sub_id, sub_sz = struct.unpack_from("<II", pc_data, h_pos)
        sub_data = pc_data[h_pos+8 : h_pos+8+sub_sz]
        if sub_id == 0x00134002:
            sub_data = patch_xbox_header_info(sub_data)
        h_inner.extend(struct.pack("<II", sub_id, len(sub_data)))
        h_inner.extend(sub_data)
        h_pos += 8 + sub_sz

    # Початковий контейнер 0x80134000 заголовок (8b) + 8b пустий + 0x80134001
    final_file = bytearray()
    final_file.extend(struct.pack("<II", 0x80134000, 0)) # розмір оновимо в кінці
    final_file.extend(b'\x00' * 8)
    final_file.extend(struct.pack("<II", 0x80134001, len(h_inner)))
    final_file.extend(h_inner)

    # 2. Конвертуємо 100% деталей виключно з ВАШОГО PC-файлу (без жодної деталі від Focus!)
    pos = pc_h_pos + 8 + pc_h_size
    offset_map_by_old_off = {}
    offset_map_by_hash = {}
    converted_count = 0

    while pos + 8 <= pc_main_end:
        cid, csize = struct.unpack_from("<II", pc_data, pos)
        full_sz = 8 + csize
        if cid == 0x80134010:
            cur_abs = len(final_file)
            final_file.extend(make_ea_padding(cur_abs, 128))

            new_abs_off = len(final_file)
            part_hash = u32(pc_data, pos + 32)
            conv_part = convert_part_container(pc_data[pos : pos + full_sz], new_abs_off)
            final_file.extend(conv_part)

            offset_map_by_old_off[pos] = (new_abs_off, len(conv_part))
            offset_map_by_hash[part_hash] = (new_abs_off, len(conv_part))
            converted_count += 1
        pos += full_sz

    # 3. Точно знаходимо таблицю 0x00134004 вже у зібраному final_file та оновлюємо ВСІ записи!
    off_table_pos = final_file.find(b'\x04\x40\x13\x00')
    if off_table_pos != -1:
        off_table_size = u32(final_file, off_table_pos + 4)
        num_entries = off_table_size // 20
        updated = 0
        for i in range(num_entries):
            e_off = off_table_pos + 8 + i * 20
            p_hash, old_off, old_sz = struct.unpack_from("<III", final_file, e_off)
            match = offset_map_by_old_off.get(old_off) or offset_map_by_hash.get(p_hash)
            if match:
                new_off, new_sz = match
                struct.pack_into("<II", final_file, e_off + 4, new_off, new_sz)
                updated += 1
        print(f"Оновлено записів у таблиці деталей: {updated} / {num_entries}")

    # Фіксуємо точний розмір головного контейнера 0x80134000
    struct.pack_into("<I", final_file, 4, len(final_file) - 8)

    # 4. Додаємо хвіст 0x80034020 з донора Android (і оновлюємо вказівник на 0x88)
    donor_tail_pos = donor.find(b'\x20\x40\x03\x80')
    info_pos = final_file.find(b'\x02\x40\x13\x00')
    if donor_tail_pos != -1:
        donor_tail_data = donor[donor_tail_pos:]
        pad_needed = align_up(len(final_file) + 16, 16) - len(final_file)
        if pad_needed < 8:
            pad_needed += 16
        final_file.extend(struct.pack("<II", 0, pad_needed - 8))
        final_file.extend(b'\x00' * (pad_needed - 8))

        new_tail_pos = len(final_file)
        final_file.extend(donor_tail_data)

        if info_pos != -1:
            struct.pack_into("<I", final_file, info_pos + 8 + 104, new_tail_pos)
    else:
        if info_pos != -1:
            struct.pack_into("<I", final_file, info_pos + 8 + 104, 0)

    with open(out_path, "wb") as f:
        f.write(final_file)
    print(f"Готово! Конвертовано {converted_count} ВАШИХ деталей із NFS-CarToolkit (0% деталей Focus). Розмір: {len(final_file)} байт.")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Використання: python3 pc2xbox_geo.py <ВАШ_PC_GEOMETRY.BIN> <ОРИГІНАЛ_ANDROID_GEOMETRY.BIN> <ВИХІДНИЙ_GEOMETRY.BIN>")
        sys.exit(1)
    _, pc_in, donor_in, xbox_out = sys.argv
    convert_geometry_file(pc_in, donor_in, xbox_out)
