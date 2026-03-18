namespace SimpleLib;

/// <summary>
/// A simple calculator class for demonstration
/// </summary>
public class Calculator
{
    /// <summary>
    /// Adds two numbers together
    /// </summary>
    public decimal Add(decimal a, decimal b)
    {
        return a + b;
    }

    /// <summary>
    /// Subtracts b from a
    /// </summary>
    public decimal Subtract(decimal a, decimal b)
    {
        return a - b;
    }

    /// <summary>
    /// Multiplies two numbers
    /// </summary>
    public decimal Multiply(decimal a, decimal b)
    {
        return a * b;
    }

    /// <summary>
    /// Divides a by b
    /// </summary>
    public decimal Divide(decimal a, decimal b)
    {
        if (b == 0)
            throw new ArgumentException("Cannot divide by zero");

        return a / b;
    }
}
