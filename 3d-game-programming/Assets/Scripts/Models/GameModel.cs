using System.Collections.Generic;
using System.Linq;

namespace PriestsAndDevils.Models
{
    public enum Side
    {
        Left,
        Right
    }

    public enum PersonType
    {
        Priest,
        Devil
    }

    public enum GameStatus
    {
        Playing,
        Won,
        Lost
    }

    public sealed class PersonModel
    {
        public int Id { get; }
        public PersonType Type { get; }
        public Side Side { get; set; }
        public bool InBoat { get; set; }

        public PersonModel(int id, PersonType type)
        {
            Id = id;
            Type = type;
            Side = Side.Left;
        }
    }

    public sealed class BoatModel
    {
        public Side Side { get; set; } = Side.Left;
        public bool IsMoving { get; set; }
        public readonly List<int> PassengerIds = new List<int>();
    }

    public sealed class GameModel
    {
        public const int PersonCount = 6;
        public const int BoatCapacity = 2;
        public const int TimeLimit = 120;

        public readonly List<PersonModel> Persons = new List<PersonModel>();
        public readonly BoatModel Boat = new BoatModel();
        public GameStatus Status { get; private set; } = GameStatus.Playing;
        public int ElapsedSeconds { get; private set; }

        public void Reset()
        {
            Persons.Clear();
            for (int i = 0; i < 3; i++) Persons.Add(new PersonModel(i, PersonType.Priest));
            for (int i = 0; i < 3; i++) Persons.Add(new PersonModel(i + 3, PersonType.Devil));
            Boat.Side = Side.Left;
            Boat.IsMoving = false;
            Boat.PassengerIds.Clear();
            Status = GameStatus.Playing;
            ElapsedSeconds = 0;
        }

        public PersonModel GetPerson(int id) => Persons.FirstOrDefault(p => p.Id == id);

        public bool TryTogglePassenger(int id, out string message)
        {
            message = string.Empty;
            if (Status != GameStatus.Playing || Boat.IsMoving)
            {
                message = "当前不能操作角色。";
                return false;
            }

            PersonModel person = GetPerson(id);
            if (person == null)
            {
                message = "找不到这个角色。";
                return false;
            }

            if (person.InBoat)
            {
                person.InBoat = false;
                Boat.PassengerIds.Remove(person.Id);
                message = "角色已下船，点击 GO 开始过河。";
                return true;
            }

            if (person.Side != Boat.Side)
            {
                message = "只能选择船所在岸边的角色。";
                return false;
            }

            if (Boat.PassengerIds.Count >= BoatCapacity)
            {
                message = "船最多搭载 2 人。";
                return false;
            }

            person.InBoat = true;
            Boat.PassengerIds.Add(person.Id);
            message = "已上船，最多再选择 1 人。";
            return true;
        }

        public bool CanMove(out string message)
        {
            message = string.Empty;
            if (Status != GameStatus.Playing)
            {
                message = Status == GameStatus.Won ? "你已经成功救下所有人！" : "游戏结束，请点击 RESTART。";
                return false;
            }
            if (Boat.PassengerIds.Count == 0 || Boat.PassengerIds.Count > BoatCapacity)
            {
                message = "船上至少需要 1 人，最多 2 人。";
                return false;
            }

            Side destination = Boat.Side == Side.Left ? Side.Right : Side.Left;
            if (!IsSafeAfterMove(destination, out message))
            {
                Status = GameStatus.Lost;
                return false;
            }

            return true;
        }

        public void MoveBoat()
        {
            Side destination = Boat.Side == Side.Left ? Side.Right : Side.Left;
            foreach (PersonModel person in Persons.Where(p => p.InBoat))
            {
                person.Side = destination;
                person.InBoat = false;
            }
            Boat.PassengerIds.Clear();
            Boat.Side = destination;
            if (Persons.All(p => p.Side == Side.Right)) Status = GameStatus.Won;
        }

        public void TickSecond()
        {
            if (Status != GameStatus.Playing) return;
            ElapsedSeconds++;
            if (ElapsedSeconds >= TimeLimit) Status = GameStatus.Lost;
        }

        public int RemainingSeconds => System.Math.Max(0, TimeLimit - ElapsedSeconds);

        public bool IsSafe(Side side)
        {
            int priests = Persons.Count(p => !p.InBoat && p.Side == side && p.Type == PersonType.Priest);
            int devils = Persons.Count(p => !p.InBoat && p.Side == side && p.Type == PersonType.Devil);
            return priests == 0 || priests >= devils;
        }

        private bool IsSafeAfterMove(Side destination, out string message)
        {
            int leftPriests = CountAllOnSide(Side.Left, PersonType.Priest);
            int leftDevils = CountAllOnSide(Side.Left, PersonType.Devil);
            int rightPriests = CountAllOnSide(Side.Right, PersonType.Priest);
            int rightDevils = CountAllOnSide(Side.Right, PersonType.Devil);

            foreach (int id in Boat.PassengerIds)
            {
                PersonModel person = GetPerson(id);
                if (person.Type == PersonType.Priest)
                {
                    if (Boat.Side == Side.Left) leftPriests--; else rightPriests--;
                    if (destination == Side.Left) leftPriests++; else rightPriests++;
                }
                else
                {
                    if (Boat.Side == Side.Left) leftDevils--; else rightDevils--;
                    if (destination == Side.Left) leftDevils++; else rightDevils++;
                }
            }

            bool leftSafe = leftPriests == 0 || leftPriests >= leftDevils;
            bool rightSafe = rightPriests == 0 || rightPriests >= rightDevils;
            if (leftSafe && rightSafe)
            {
                message = string.Empty;
                return true;
            }

            message = "危险：恶魔数量超过了某一侧的牧师数量，牧师会被吃掉！";
            return false;
        }

        private int CountAllOnSide(Side side, PersonType type)
        {
            return Persons.Count(p => p.Side == side && p.Type == type);
        }
    }
}
