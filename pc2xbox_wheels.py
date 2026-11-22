import argparse
import struct
import sys

WHEEL_BLOCK_START = 0x120
WHEEL_BLOCK_SIZE = 0xC0  # 192 байти (4 колеса по 48 байт)

def find_car_offset(buf, car_name):
    target = car_name.upper().encode("ascii")
    sig = target + b"\x00"
    start = 0
    while True:
        off = buf.find(sig, start)
        if off == -1:
            print(f"Помилка: запис CarTypeInfo для '{car_name.upper()}' не знайдено у файлі.")
            sys.exit(1)
        # Перевіряємо, що це саме структура CarTypeInfo (на +64 йде шлях 'CARS\\...')
        if off + 70 <= len(buf) and buf[off + 64 : off + 69] == b"CARS\\":
            return off
        start = off + 1

def print_wheels(buf, car_off, label):
    names = ("Front-Left ", "Front-Right", "Rear-Right ", "Rear-Left  ")
    print(f"  [{label}]")
    for idx, wname in enumerate(names):
        w_off = car_off + WHEEL_BLOCK_START + idx * 48
        x, _, z = struct.unpack_from("<fff", buf, w_off)
        rad, width = struct.unpack_from("<ff", buf, w_off + 16)
        y1, y2 = struct.unpack_from("<ff", buf, w_off + 28)
        diam = rad * 2.0
        print(f"    {wname}: X={x:+.4f}, Y={y1:+.4f} (outer={y2:+.4f}), Z={z:+.4f} | W={width:.4f}m, D={diam:.4f}m (R={rad:.4f}m)")

def cmd_info(args):
    with open(args.file, "rb") as f:
        data = f.read()
    car_off = find_car_offset(data, args.car)
    print(f"Параметри коліс для {args.car.upper()} (зміщення 0x{car_off:X}):")
    print_wheels(data, car_off, args.file)

def cmd_copy(args):
    with open(args.pc_file, "rb") as f:
        pc_data = f.read()
    with open(args.android_file, "rb") as f:
        and_data = bytearray(f.read())

    pc_off = find_car_offset(pc_data, args.car)
    and_off = find_car_offset(and_data, args.car)

    print(f"Перенесення налаштувань коліс для {args.car.upper()}:")
    print_wheels(and_data, and_off, "БУЛО в Android")
    print_wheels(pc_data, pc_off, "СТАЛО з PC")

    src_slice = pc_data[pc_off + WHEEL_BLOCK_START : pc_off + WHEEL_BLOCK_START + WHEEL_BLOCK_SIZE]
    and_data[and_off + WHEEL_BLOCK_START : and_off + WHEEL_BLOCK_START + WHEEL_BLOCK_SIZE] = src_slice

    with open(args.out_file, "wb") as f:
        f.write(and_data)
    print(f"Готово! Збережено у {args.out_file}.")

def cmd_edit(args):
    with open(args.in_file, "rb") as f:
        data = bytearray(f.read())

    car_off = find_car_offset(data, args.car)
    print(f"Редагування коліс для {args.car.upper()}:")
    print_wheels(data, car_off, "БУЛО")

    # Індекси коліс: 0=Front-Left (+Y), 1=Front-Right (-Y), 2=Rear-Right (-Y), 3=Rear-Left (+Y)
    for idx in range(4):
        w_off = car_off + WHEEL_BLOCK_START + idx * 48
        is_front = idx in (0, 1)
        y_sign = 1.0 if idx in (0, 3) else -1.0

        # 1. База (X) на +0
        if is_front and args.fx is not None:
            struct.pack_into("<f", data, w_off, abs(args.fx))
        elif (not is_front) and args.rx is not None:
            struct.pack_into("<f", data, w_off, -abs(args.rx))

        # 2. Виліт / ширина колії (Y) на +28 та +32
        target_y = args.fy if is_front else args.ry
        if target_y is None:
            target_y = args.y
        if target_y is not None:
            signed_y = y_sign * abs(target_y)
            struct.pack_into("<ff", data, w_off + 28, signed_y, signed_y)

        # 3. Висота (Z) на +8
        target_z = args.fz if is_front else args.rz
        if target_z is None:
            target_z = args.z
        if target_z is not None:
            struct.pack_into("<f", data, w_off + 8, target_z)

        # 4. Діаметр (D = 2*R) або Радіус (R) на +16 (єдиний для всіх 4 коліс)
        if args.d is not None:
            struct.pack_into("<f", data, w_off + 16, abs(args.d) / 2.0)
        elif args.r is not None:
            struct.pack_into("<f", data, w_off + 16, abs(args.r))

        # 5. Ширина покришки (W) на +20
        target_w = args.fw if is_front else args.rw
        if target_w is None:
            target_w = args.w
        if target_w is not None:
            struct.pack_into("<f", data, w_off + 20, abs(target_w))

    print_wheels(data, car_off, "СТАЛО")
    with open(args.out_file, "wb") as f:
        f.write(data)
    print(f"Готово! Збережено у {args.out_file}.")

def main():
    parser = argparse.ArgumentParser(description="Редактор та конвертер коліс NFS Underground (GlobalB.lzc)")
    sub = parser.add_subparsers(dest="mode", required=True)

    # 1. info
    p_info = sub.add_parser("info", help="Показати поточні параметри коліс авто")
    p_info.add_argument("file", help="Шлях до GlobalB.lzc")
    p_info.add_argument("car", help="Код авто (наприклад FOCUS, SKYLINE, CIVIC)")
    p_info.set_defaults(func=cmd_info)

    # 2. copy
    p_copy = sub.add_parser("copy", help="Перенести параметри коліс із PC GlobalB.lzc в Android/Xbox GlobalB.lzc")
    p_copy.add_argument("pc_file", help="Шлях до PC GlobalB.lzc")
    p_copy.add_argument("android_file", help="Шлях до Android/Xbox GlobalB.lzc")
    p_copy.add_argument("out_file", help="Вихідний файл GlobalB.lzc")
    p_copy.add_argument("car", help="Код авто (наприклад FOCUS)")
    p_copy.set_defaults(func=cmd_copy)

    # 3. edit
    p_edit = sub.add_parser("edit", help="Ручне налаштування позиції, ширини та діаметра коліс")
    p_edit.add_argument("in_file", help="Вхідний GlobalB.lzc")
    p_edit.add_argument("out_file", help="Вихідний GlobalB.lzc")
    p_edit.add_argument("car", help="Код авто (наприклад FOCUS, SUPRA)")

    p_edit.add_argument("--fx", type=float, help="Передня вісь X (база вперед)")
    p_edit.add_argument("--rx", type=float, help="Задня вісь X (база назад)")

    p_edit.add_argument("--y", type=float, help="Виліт коліс Y для обох осей")
    p_edit.add_argument("--fy", type=float, help="Передня колія Y")
    p_edit.add_argument("--ry", type=float, help="Задня колія Y")

    p_edit.add_argument("--z", type=float, help="Висота Z для обох осей")
    p_edit.add_argument("--fz", type=float, help="Висота передніх коліс Z")
    p_edit.add_argument("--rz", type=float, help="Висота задніх коліс Z")

    p_edit.add_argument("--w", type=float, help="Ширина коліс для обох осей")
    p_edit.add_argument("--fw", type=float, help="Ширина передніх коліс")
    p_edit.add_argument("--rw", type=float, help="Ширина задніх коліс")

    p_edit.add_argument("--d", type=float, help="Зовнішній діаметр коліс D")
    p_edit.add_argument("--r", type=float, help="Радіус коліс R (половина діаметра)")

    p_edit.set_defaults(func=cmd_edit)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
