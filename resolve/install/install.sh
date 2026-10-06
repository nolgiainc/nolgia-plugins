#!/bin/sh
# NOLGIA for DaVinci Resolve: copies the plugin into Resolve's Scripts folder for this user.
set -e
here=$(cd "$(dirname "$0")" && pwd)
case "$(uname -s)" in
  Darwin) target="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility" ;;
  *) target="$HOME/.local/share/DaVinciResolve/Fusion/Scripts/Utility" ;;
esac
if [ ! -f "$here/Utility/NOLGIA.py" ]; then
  echo "Could not find the Utility folder next to this installer. Unzip the whole download first."
  exit 1
fi
mkdir -p "$target"
cp "$here/Utility/NOLGIA.py" "$here/Utility/nolgia_resolve.zip" "$target/"
echo "NOLGIA is installed in:"
echo "  $target"
echo "Restart DaVinci Resolve, then open Workspace > Scripts > NOLGIA."
