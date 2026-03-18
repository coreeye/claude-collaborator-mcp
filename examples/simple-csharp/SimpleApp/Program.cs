using SimpleLib;

/// <summary>
/// Main application entry point
/// </summary>
class Program
{
    static void Main(string[] args)
    {
        var calc = new Calculator();

        Console.WriteLine("Simple Calculator Demo");
        Console.WriteLine("----------------------");

        var a = 10m;
        var b = 5m;

        Console.WriteLine($"{a} + {b} = {calc.Add(a, b)}");
        Console.WriteLine($"{a} - {b} = {calc.Subtract(a, b)}");
        Console.WriteLine($"{a} * {b} = {calc.Multiply(a, b)}");
        Console.WriteLine($"{a} / {b} = {calc.Divide(a, b)}");
    }
}
