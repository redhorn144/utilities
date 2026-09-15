# nekAnimation

Renders nek5000 / nekRS field files (`case0.f*****`) to PNG frames through a
scene saved from the ParaView GUI, ready for ffmpeg. Runs serially on a laptop
or under MPI on a cluster (Aurora job script included). Targets ParaView 6.0.0.

## Workflow

1. Put the field files in `data/` (git-ignored) or anywhere you like.
2. Generate the `.nek5000` metadata file the reader needs:

       python3 nek_render.py --dry-run --data data

   (No ParaView needed; plain `python3` is fine here.)
3. Open `data/case.nek5000` in the ParaView GUI. When asked which reader to
   use, pick **Nek5000 Files** (the native reader, see below). Build the scene
   -- filters, colormaps, camera, lights, an *Annotate Time* filter if you
   want a clock -- then **File > Save State** as `scene.pvsm`.
4. Render:

       pvbatch --force-offscreen-rendering nek_render.py scene.pvsm --data data --out frames

   The reader inside the state is repointed at the `.nek5000` found in `--data`,
   so the scene can be saved on a laptop and rendered on a cluster, or reused
   against another run of the same case. `pvpython` works too.
5. Stitch with the ffmpeg command the script prints, e.g.

       ffmpeg -framerate 30 -pattern_type glob -i 'frames/case_*.png' \
           -c:v libx264 -pix_fmt yuv420p -vf "pad=ceil(iw/2)*2:ceil(ih/2)*2" case.mp4

## Options

| flag | meaning |
| --- | --- |
| `--data DIR` | directory holding `<case>0.f*****` files (default `data`) |
| `--case NAME` | case prefix; auto-detected when only one is present |
| `--nek5000 PATH` | use a hand-written `.nek5000` instead of generating one |
| `--out DIR` | frame directory (default `frames`) |
| `--resolution W H` | frame size; default is the view size saved in the state |
| `--start / --end / --stride` | subset of timesteps, by index |
| `--part K/N` | render only chunk K of N, so N independent jobs share one movie |
| `--layout` | capture the whole layout (all views) instead of the active view |
| `--force` | re-render frames that already exist (otherwise they are skipped, so a killed run resumes) |
| `--dry-run` | write the `.nek5000` and list the frames, no ParaView needed |

## 3D and large field files

Run under MPI; each rank reads and renders its share of the elements:

    mpiexec -n 24 pvbatch --force-offscreen-rendering nek_render.py scene.pvsm

`pvbatch` executes the script on rank 0 only and drives the other ranks as
servers, so the script needs nothing special (do **not** pass `--symmetric`).
What matters is the scene:

* **Use the native reader.** ParaView offers two readers for `.nek5000`:
  *Nek5000 Files* (`Nek5000Reader`) splits the mesh across ranks; the VisIt
  one (`VisItNek5000Reader`) loads everything on a single rank. The script
  warns when a multi-rank run hits a VisIt reader. Check with
  `grep -o 'type="[A-Za-z]*Nek5000Reader"' scene.pvsm`.
* **Read only what you draw.** In the reader's properties, untick point
  arrays the scene does not use; the selection is saved in the state and cuts
  I/O, which dominates the time per frame. Leave *Merge points to clean grid*
  off unless a downstream filter really needs a conforming mesh.
* **Color by `Velocity` -> Magnitude, not by the reader's `Velocity Magnitude`
  array.** The native reader only computes that derived array while it is
  also reading `Velocity`; ticked on its own it is filled with misaligned
  velocity components (a colormap range with negative values is the tell).
  The script enables `Velocity` for you if the state has it the wrong way.
* **Reduce before rendering.** Slice, Contour, Clip, Extract Surface -- render
  a 2D/surface representation rather than the full volume. Volume rendering
  and Stream Tracer scale badly with element count.
* `--stride` is the cheap lever on total time; `--resolution` on per-frame time.

### Building the scene for a case that does not fit on the laptop

Either works; the script overrides the reader path in the state regardless.

* **Client/server**: connect the Windows GUI to `pvserver` on Aurora
  (ALCF docs: *Aurora > Visualization > ParaView*), open the `.nek5000` on
  flare, build the scene, save state locally. Client and server versions
  must match. If remote rendering comes back blank, `pvserver` has the same
  backend problem as the batch job (see *Rendering on the CPU* below) and
  wants `--opengl-window-backend OSMesa` too.
* **One timestep locally**: copy a single `case0.f00001` into a folder on the
  laptop, run `--dry-run` there (a `.nek5000` with `numtimesteps: 1`), build the
  scene on that step.

## Aurora

Data and frames live on `/lus/flare/projects/<project>/...`, not in `$HOME`.
Clone this repo there (or just copy `nek_render.py` and `render.pbs`).

Check which ParaView the system provides:

    module use /soft/modulefiles
    module avail paraview

If a module matches the version the scene was saved with, use it (multi-node).
Submit from the directory holding `scene.pvsm` and `nek_render.py`; the job
`cd`s there. `DATA` is read in place (never copy field files) and must be
writable, since the `.nek5000` is written next to them:

    qsub -v SCENE=scene.pvsm,DATA=/lus/flare/projects/P/run,OUT=/lus/flare/projects/P/run/frames,PV_MODULE=paraview/paraview-6.0.0 render.pbs

Otherwise unpack the Kitware Linux tarball in `$HOME` and point the job at it
(single node, uses the tarball's own `mpiexec`; since 6.0 there is one Linux
binary that carries the X, EGL and OSMesa backends):

    curl -L -O https://www.paraview.org/files/v6.0/ParaView-6.0.0-MPI-Linux-Python3.12-x86_64.tar.gz
    qsub -v SCENE=scene.pvsm,DATA=...,OUT=...,PV_HOME=$HOME/opt/ParaView-6.0.0-MPI-Linux-Python3.12-x86_64 render.pbs

Other knobs are documented at the top of `render.pbs` (`RPN` ranks per node,
`PART`, `EXTRA` for further `nek_render.py` options, `BACKEND`,
`LP_NUM_THREADS`). Override PBS directives on the command line, e.g.
`qsub -q debug -l walltime=00:30:00 -l select=2 ...`; the project account is
the `#PBS -A` line in the script.

Suggested first run: `-q debug` with `EXTRA="--end 3"`, then look at the
`.o` file for the rank count and seconds per frame before sizing the real job.
Expect lines like `reader 'case.nek5000' point arrays: Velocity, ...` and
`[1/3] step 0 ...`; a job that goes quiet after `rendering on N rank(s)`
has no working OpenGL context (see below).

### Rendering on the CPU

Aurora's Intel Max GPUs have no OpenGL driver. Left to itself, ParaView tries
X (no display), then EGL, which "succeeds" with a Mesa llvmpipe context at
OpenGL 3.1 -- too old for ParaView's shaders -- and the job then hangs or
writes nothing. `render.pbs` therefore passes `--opengl-window-backend OSMesa`
(`BACKEND`), which renders in software with a proper 4.x core context, and
caps Mesa's threads per rank with `LP_NUM_THREADS` (default 8) so 12 ranks do
not each spawn 100 threads. Do not try to fix this with
`MESA_GL_VERSION_OVERRIDE`; the system Mesa crashes with a bus error.

To check a build by hand, from an interactive node
(`qsub -I -A <project> -q debug -l select=1 -l walltime=00:15:00 -l filesystems=home:flare`):

    module use /soft/modulefiles && module load paraview/paraview-6.0.0
    pvpython --opengl-window-backend OSMesa -c "import paraview.simple as pv; pv.Show(pv.Sphere()); pv.SaveScreenshot('test.png'); print(pv.GetActiveView().GetRenderWindow().ReportCapabilities())" | grep -E 'OpenGL (renderer|version)'

Wanted: a `Core Profile` version line and a sphere in `test.png`. If the
renderer is `softpipe` (Mesa's slow reference rasterizer) rather than
`llvmpipe`, try adding `GALLIUM_DRIVER=llvmpipe` to the `qsub -v` list after
confirming it renders the sphere too. With CPU rendering, fewer ranks with
more `LP_NUM_THREADS` each may beat the 12x8 default; the 3-frame run is the
place to compare.

### Long movies

To spread a long movie over several jobs, submit a job array; each sub-job
renders its own contiguous chunk:

    qsub -J 1-4 -v ...,NPARTS=4 render.pbs

Stitch on Aurora if `ffmpeg` is available, or pull the frames back:

    rsync -a aurora.alcf.anl.gov:/lus/flare/projects/P/frames/ frames/

## Notes

* Frames are named after the nek file number (`case_00042.png` <- `case0.f00042`),
  so ffmpeg is driven with a glob rather than `%05d` and `--stride` works.
* The nek5000 reader only understands a contiguous range of steps starting at
  the first one; files after a gap in the numbering are ignored with a warning.
* `filetemplate` in the generated `.nek5000` is absolute because the reader
  uses it verbatim. Regenerate (just rerun the script) if the data moves.
* The native reader applies its array selection by position, in file order
  (`Velocity`, `Velocity Magnitude`, `Pressure`, `Temperature`, `S01`...),
  while the GUI saves it alphabetically, so a state loaded as-is reads the
  wrong arrays and the scene renders solid white. The script re-sorts the
  selection into a copy of the state, `<out>/<scene>.loaded.pvsm`, loads that,
  and stops with an error if a reader still fails to produce an enabled array.
* The state is loaded with `location=SERVERS`; the default (`CLIENT`)
  deadlocks in MPI `pvbatch` with ParaView 6.0.0.
