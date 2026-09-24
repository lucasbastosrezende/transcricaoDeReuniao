// Atalho de duplo clique para o Transcritor pt-BR.
//
// Não contém lógica de transcrição: mostra um menu e chama o iniciar.bat, que
// prepara o ambiente Python (venv, dependências, FFmpeg) e roda o script certo.
// Arquivos arrastados para cima do .exe vão direto para a transcrição crua.
//
// Compilar: launcher\compilar.bat  (usa o csc.exe que já vem com o Windows)
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class RodarTranscricao
{
    private static string raiz;

    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;
        Console.Title = "Transcritor pt-BR";
        raiz = AppDomain.CurrentDomain.BaseDirectory;

        if (!File.Exists(Path.Combine(raiz, "iniciar.bat")))
        {
            Console.WriteLine("Não encontrei o iniciar.bat ao lado deste programa.");
            Console.WriteLine("Deixe o rodar-transcricao.exe na pasta raiz do projeto.");
            Pausar();
            return 1;
        }

        // Arquivos soltos em cima do ícone: sem menu, transcrição crua direto.
        if (args.Length > 0)
        {
            var partes = new StringBuilder("transcricao_crua.py");
            foreach (var arquivo in args)
                partes.Append(" \"").Append(arquivo).Append('"');
            int codigo = Iniciar(partes.ToString());
            Pausar();
            return codigo;
        }

        while (true)
        {
            Console.Clear();
            Console.WriteLine();
            Console.WriteLine("  ================================================================");
            Console.WriteLine("     TRANSCRITOR pt-BR  -  gratuito, offline e sem limites");
            Console.WriteLine("  ================================================================");
            Console.WriteLine();
            Console.WriteLine("   [1]  Transcrição completa (abre no navegador)");
            Console.WriteLine("        falantes, resumo, legendas, Word, PDF, busca...");
            Console.WriteLine();
            Console.WriteLine("   [2]  Transcrição crua - só o texto");
            Console.WriteLine("        sem minutagem, sem falantes, num .txt. Mais rápida.");
            Console.WriteLine();
            Console.WriteLine("   [3]  Abrir a pasta com as transcrições");
            Console.WriteLine();
            Console.WriteLine("   [0]  Sair");
            Console.WriteLine();
            Console.WriteLine("  Dica: arraste um vídeo ou áudio para cima do rodar-transcricao.exe");
            Console.WriteLine("        para fazer a transcrição crua direto.");
            Console.WriteLine();
            Console.Write("  Escolha uma opção: ");

            var tecla = Console.ReadKey(true).KeyChar;
            Console.WriteLine(tecla);
            Console.WriteLine();

            switch (tecla)
            {
                case '1':
                    // Mesma janela: o servidor fica rodando até ela ser fechada.
                    return Iniciar("");
                case '2':
                    Iniciar("transcricao_crua.py");
                    Pausar();
                    break;
                case '3':
                    var pasta = Path.Combine(raiz, "outputs");
                    Directory.CreateDirectory(pasta);
                    Process.Start("explorer.exe", "\"" + pasta + "\"");
                    break;
                case '0':
                case '\u001b':
                    return 0;
            }
        }
    }

    private static int Iniciar(string argumentos)
    {
        var bat = Path.Combine(raiz, "iniciar.bat");
        // /s com aspas externas: o cmd preserva as aspas internas dos caminhos.
        var info = new ProcessStartInfo("cmd.exe", "/s /c \"\"" + bat + "\" " + argumentos + "\"")
        {
            UseShellExecute = false,
            WorkingDirectory = raiz,
        };
        using (var processo = Process.Start(info))
        {
            processo.WaitForExit();
            return processo.ExitCode;
        }
    }

    private static void Pausar()
    {
        Console.WriteLine();
        Console.Write("  Pressione qualquer tecla para voltar...");
        Console.ReadKey(true);
    }
}
