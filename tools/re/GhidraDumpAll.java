// Dump decompilation of every function to one file, with a function index.
// Functions referencing "interesting" strings are decompiled first into <out>.priority.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.data.*;
import java.io.*;
import java.util.*;
import java.util.regex.*;

public class DumpAll extends GhidraScript {
    PrintWriter w; DecompInterface ifc;

    void dump(Function f, PrintWriter out, Set<String> tags) {
        out.println("//// ==== FUNCTION " + f.getName() + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses()
            + (tags == null ? "" : " refs=" + tags));
        DecompileResults r = ifc.decompileFunction(f, 90, monitor);
        if (r != null && r.decompileCompleted() && r.getDecompiledFunction() != null) {
            out.println(r.getDecompiledFunction().getC());
        } else {
            out.println("// decompile failed: " + (r == null ? "null" : r.getErrorMessage()));
        }
    }

    @Override
    public void run() throws Exception {
        String out = getScriptArgs().length > 0 ? getScriptArgs()[0] : "/home/claude/re/decomp.txt";
        ifc = new DecompInterface();
        ifc.setOptions(new DecompileOptions());
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();

        // index
        PrintWriter idx = new PrintWriter(new BufferedWriter(new FileWriter(out + ".index")));
        for (Function f : fm.getFunctions(true)) {
            idx.println(f.getEntryPoint() + "\t" + f.getName() + "\t" + f.getBody().getNumAddresses());
        }
        idx.close();

        // strings of interest -> referencing functions
        Pattern pat = Pattern.compile("ProtoTools|Aurora|Particle|Flurry|Aura|pulse|Pulse|Upmix|Surround|Artwork|ColorBin|Background|Visualization|Continuum|SourceInstanceNode|ReferenceCircle|Orb|Capsule|spread|Spread|emitter|handle|Handle|Smooth|FFT|Resample|stems|VisualizationData|AudioAnalysis|metallib|deltaTime|Aerosol|Loudness");
        Map<Function, Set<String>> prio = new LinkedHashMap<>();
        PrintWriter sl = new PrintWriter(new BufferedWriter(new FileWriter(out + ".strings")));
        DataIterator di = currentProgram.getListing().getDefinedData(true);
        int ns = 0;
        while (di.hasNext() && !monitor.isCancelled()) {
            Data d = di.next();
            if (!(d.getDataType() instanceof StringDataType || d.getDataType() instanceof StringUTF8DataType
                  || d.getDataType() instanceof TerminatedStringDataType || d.getDataType().getName().toLowerCase().contains("string"))) continue;
            Object v = d.getValue();
            if (v == null) continue;
            String s = v.toString();
            if (!pat.matcher(s).find()) continue;
            ns++;
            ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(d.getAddress());
            while (ri.hasNext()) {
                Reference ref = ri.next();
                Function f = fm.getFunctionContaining(ref.getFromAddress());
                if (f == null) continue;
                sl.println(f.getEntryPoint() + "\t" + d.getAddress() + "\t" + s.replace('\n',' '));
                prio.computeIfAbsent(f, k -> new LinkedHashSet<>()).add(s.length() > 60 ? s.substring(0, 60) : s);
            }
        }
        sl.close();
        println("interesting strings: " + ns + ", priority functions: " + prio.size());

        PrintWriter pw = new PrintWriter(new BufferedWriter(new FileWriter(out + ".priority")));
        int n = 0;
        for (Map.Entry<Function, Set<String>> e : prio.entrySet()) {
            if (monitor.isCancelled()) break;
            dump(e.getKey(), pw, e.getValue());
            if (++n % 50 == 0) { pw.flush(); println("priority decompiled " + n); }
        }
        pw.close();
        println("priority done " + n);

        w = new PrintWriter(new BufferedWriter(new FileWriter(out)));
        n = 0;
        for (Function f : fm.getFunctions(true)) {
            if (monitor.isCancelled()) break;
            if (f.isThunk()) continue;
            dump(f, w, null);
            if (++n % 500 == 0) { w.flush(); println("decompiled " + n); }
        }
        w.close();
        println("done " + n);
    }
}
