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
* **Reduce before rendering.** Slice, Contour, Clip, Extract Surface -- render
  a 2D/surface representation rather than the full volume. Volume rendering
  and Stream Tracer scale badly with element count.
* `--stride` is the cheap lever on total time; `--resolution` on per-frame time.

### Building the scene for a case that does not fit on the laptop

Either works; the script overrides the reader path in the state regardless.

* **Client/server**: connect the Windows GUI to `pvserver` on Aurora
  (ALCF docs: *Aurora > Visualization > ParaView*), open the `.nek5000` on
  flare, build the scene, save state locally. Client and server versions
  must match.
* **One timestep locally**: copy a single `case0.f00001` into a folder on the
  laptop, run `--dry-run` there (a `.nek5000` with `numtimesteps: 1`), build the
  scene on that step.

## Aurora

Data and frames live on `/lus/flare/projects/<project>/...`, not in `$HOME`.
Clone this repo there (or just copy `nek_render.py` and `render.pbs`).

Check which ParaView the system provides:

    module use /soft/modulefiles
    module avail paraview

If a module matches the version the scene was saved with, use it (multi-node):

    qsub -v SCENE=scene.pvsm,DATA=/lus/flare/projects/P/run,OUT=/lus/flare/projects/P/frames,PV_MODULE=paraview/paraview-6.0.0 render.pbs

Otherwise unpack the matching Kitware Linux tarball in `$HOME` and point the
job at it (single node, uses the tarball's own `mpiexec`):

    qsub -v SCENE=scene.pvsm,DATA=...,OUT=...,PV_HOME=$HOME/opt/ParaView-6.0.0-MPI-Linux-Python3.12-x86_64 render.pbs

Other knobs are documented at the top of `render.pbs` (`RPN` ranks per node,
`PART`, `EXTRA` for further `nek_render.py` options). Override PBS directives
on the command line, e.g. `qsub -q debug -l walltime=00:30:00 -l select=2 ...`.

Suggested first run: `-q debug` with `EXTRA="--end 3"`, then look at the
`.o` file for the rank count and seconds per frame before sizing the real job.
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
* The state is loaded with `location=SERVERS`; the default (`CLIENT`)
  deadlocks in MPI `pvbatch` with ParaView 6.0.0.
