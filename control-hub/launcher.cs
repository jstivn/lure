using System;
using System.Diagnostics;
using System.IO;

class Program
{
    static void Main()
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string appDir = Path.Combine(baseDir, "apps", "control-hub");
        string pythonw = Path.Combine(baseDir, "apps", "meta-lead-gen", "venv", "Scripts", "pythonw.exe");
        string script = Path.Combine(appDir, "app.py");

        if (!File.Exists(pythonw))
        {
            pythonw = "pythonw.exe";
        }

        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = pythonw;
        psi.Arguments = "\"" + script + "\"";
        psi.WorkingDirectory = appDir;
        psi.UseShellExecute = false;

        try
        {
            Process p = Process.Start(psi);
            if (p != null)
            {
                p.WaitForExit();
            }
        }
        catch (Exception ex)
        {
            System.Windows.Forms.MessageBox.Show("Ошибка запуска Lure Outreach System:\n" + ex.Message, "Lure", System.Windows.Forms.MessageBoxButtons.OK, System.Windows.Forms.MessageBoxIcon.Error);
        }
    }
}