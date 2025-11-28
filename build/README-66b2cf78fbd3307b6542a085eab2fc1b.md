# Example Viewer Template

This template repository is intended to allow easy instantiation of an example viewer for jupyterlab notebooks.
External repositories can be added via git submodules to the external_notebooks folder.
The github action will traverse available notebooks and try to extract metadata information as well as build them with Jupyterbook (v2 and MYST).
The build package is then deployed on github pages.