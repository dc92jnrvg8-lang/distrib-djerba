# SPDX-License-Identifier: GPL-3.0-or-later
"""Lanceur seccomp-bpf du bac à sable (repli sans systemd).

Usage : python3 -I seccomp_launch.py -- PROGRAMME [ARGUMENTS…]

Installe un filtre seccomp-bpf (libseccomp, python3-seccomp) puis remplace le
processus par le programme demandé : le filtre est hérité et ne peut plus
être retiré (no_new_privs). Appels système refusés (EPERM) :

* sockets autres que UNIX (aucun réseau : AF_INET, AF_INET6, AF_PACKET,
  AF_NETLINK…) ;
* débogage et lecture d'autres processus (ptrace, process_vm_*,
  perf_event_open, kcmp) ;
* montages et espaces de noms (mount, umount2, pivot_root, chroot, unshare,
  setns, clone avec CLONE_NEWUSER/NEWNS/NEWNET ; clone3 répond ENOSYS) ;
* noyau (modules, kexec, bpf, keyctl, add_key, request_key, syslog,
  userfaultfd, io_uring), horloge, redémarrage, swap, quotas, personality.

Volontairement autonome : il n'importe que la bibliothèque standard et
seccomp (exécuté avec -I, aucune influence de l'environnement).
"""

import errno
import os
import socket
import sys

DENIED = (
    "ptrace", "process_vm_readv", "process_vm_writev", "perf_event_open", "kcmp",
    "mount", "umount2", "pivot_root", "chroot", "unshare", "setns", "fsopen", "fsmount", "move_mount", "open_tree",
    "init_module", "finit_module", "delete_module", "kexec_load", "kexec_file_load", "bpf",
    "keyctl", "add_key", "request_key", "syslog", "userfaultfd", "io_uring_setup", "io_uring_enter",
    "io_uring_register", "acct", "quotactl", "swapon", "swapoff", "reboot", "settimeofday", "clock_settime",
    "clock_adjtime", "adjtimex", "personality", "open_by_handle_at", "name_to_handle_at", "iopl", "ioperm",
)
CLONE_NEWUSER = 0x10000000
CLONE_NEWNS = 0x00020000
CLONE_NEWNET = 0x40000000


def install() -> None:
    import seccomp

    rule = seccomp.ERRNO(errno.EPERM)
    sandbox = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    for name in DENIED:
        try:
            sandbox.add_rule(rule, name)
        except (RuntimeError, ValueError, OSError):
            continue  # appel inexistant sur cette architecture
    # clone3 passe ses options par pointeur (non filtrable) : ENOSYS force la libc à
    # se replier sur clone(), dont les options sont filtrées ci-dessous.
    sandbox.add_rule(seccomp.ERRNO(errno.ENOSYS), "clone3")
    # Réseau : seules les sockets UNIX (domaine AF_UNIX = 1) sont permises.
    sandbox.add_rule(rule, "socket", seccomp.Arg(0, seccomp.NE, socket.AF_UNIX))
    sandbox.add_rule(rule, "socketpair", seccomp.Arg(0, seccomp.NE, socket.AF_UNIX))
    # clone() créant de nouveaux espaces de noms : refusé.
    for flag in (CLONE_NEWUSER, CLONE_NEWNS, CLONE_NEWNET):
        sandbox.add_rule(rule, "clone", seccomp.Arg(0, seccomp.MASKED_EQ, flag, flag))
    sandbox.load()


def main(argv: list[str]) -> int:
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        print("usage : seccomp_launch.py -- PROGRAMME [ARGUMENTS…]", file=sys.stderr)
        return 2
    program = argv[argv.index("--") + 1:]
    install()
    os.execvp(program[0], program)
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
