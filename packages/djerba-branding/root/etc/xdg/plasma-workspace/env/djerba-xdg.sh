# Djerba OS — identité « À propos de ce système » de la session Plasma.
# /etc/xdg/kcm-about-distrorc appartient à desktop-base (Debian) : Djerba place
# la sienne dans /etc/xdg/djerba, prioritaire dans la cascade XDG, au lieu de
# lui disputer le fichier (conflit dpkg). Sourcé par startplasma uniquement :
# les autres sessions (GNOME…) gardent un environnement intact.
case ":${XDG_CONFIG_DIRS:-}:" in
    *:/etc/xdg/djerba:*) ;;
    *) export XDG_CONFIG_DIRS="/etc/xdg/djerba:${XDG_CONFIG_DIRS:-/etc/xdg}" ;;
esac
